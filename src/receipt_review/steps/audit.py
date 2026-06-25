from __future__ import annotations

from decimal import Decimal, InvalidOperation

from openai import OpenAI

from receipt_review.config import load_settings
from receipt_review.schemas import AuditDecision, AuditJudgment, ReceiptDetails, ReceiptItem
from receipt_review.llm.openai_client import create_structured_response, get_client


AUDIT_INSTRUCTIONS = """
You judge two audit flags for a receipt from its structured details.

Use this v0 audit policy:
- Travel-related expenses include gas, fuel, hotel, airfare, and car rental. If the receipt shows gas or fuel, not_travel_related is false.
- not_travel_related is true when the merchant and purchased items are not travel-related.
- handwritten_x is true when handwritten_notes contains an explicit standalone handwritten "X".

Give concise reasoning grounded only in the structured receipt details provided.
""".strip()

# Deterministic policy values. amount_over_limit, math_error, and needs_audit
# are pure arithmetic/boolean logic, so they are computed in code instead of
# being requested from the model.
AUDIT_TOTAL_LIMIT = Decimal("50.00")
MONEY_TOLERANCE = Decimal("0.01")


def parse_amount(value: str | None) -> Decimal | None:
    if not isinstance(value, str):
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    # Accounting notation: (3.00) means -3.00, common on discount lines.
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1].strip()
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def check_amount_over_limit(receipt_details: ReceiptDetails) -> bool:
    total = parse_amount(receipt_details.total)
    return total is not None and total > AUDIT_TOTAL_LIMIT


def line_total_inconsistency(item: ReceiptItem, index: int) -> str | None:
    """Return a problem description when no reading of the line is arithmetically consistent.

    Extraction field semantics are unreliable at the line level (quantities read
    from product names, line totals duplicated into item_price), so a line only
    counts as inconsistent when its total matches neither price-times-quantity
    nor the bare price for any available price field.
    """

    quantity = parse_amount(item.quantity)
    line_total = parse_amount(item.total)
    prices = [price for price in (parse_amount(item.sale_price), parse_amount(item.item_price)) if price is not None]
    if quantity is None or line_total is None or not prices:
        return None

    # Unit prices and quantities carry more digits than cents (fuel: 4.209 x
    # 20.67), so allow small relative error from a misread trailing digit.
    tolerance = max(MONEY_TOLERANCE, abs(line_total) * Decimal("0.005"))
    candidates = [price * quantity for price in prices] + prices
    if any(abs(candidate - line_total) <= tolerance for candidate in candidates):
        return None
    return f"items[{index}]: total {line_total} matches no consistent reading of price x quantity"


def known_item_totals(receipt_details: ReceiptDetails) -> tuple[list[Decimal], bool]:
    item_totals: list[Decimal] = []
    every_item_total_known = bool(receipt_details.items)
    for item in receipt_details.items:
        line_total = parse_amount(item.total)
        if line_total is None:
            every_item_total_known = False
        else:
            item_totals.append(line_total)
    return item_totals, every_item_total_known


def check_math_error(receipt_details: ReceiptDetails) -> tuple[bool, list[str]]:
    """Flag summary-level arithmetic failures: the printed receipt math is wrong.

    The summary fields are the trusted reading: subtotal + tax must equal
    total. Item lines are consulted only when subtotal is missing, because
    item-level extraction is noisy (duplicated lines, tax and tender lines
    read as items) and disagreement there is reported as
    line_item_extraction_warning instead. Every check only runs when all of its
    inputs are present and parseable, so missing values never count as a math
    error on their own.
    """

    problems: list[str] = []
    subtotal = parse_amount(receipt_details.subtotal)
    tax = parse_amount(receipt_details.tax)
    total = parse_amount(receipt_details.total)

    if subtotal is not None and tax is not None and total is not None:
        if abs(subtotal + tax - total) > MONEY_TOLERANCE:
            problems.append(f"subtotal {subtotal} + tax {tax} != total {total}")

    if subtotal is None and total is not None:
        item_totals, every_item_total_known = known_item_totals(receipt_details)
        if every_item_total_known:
            expected_total = sum(item_totals, Decimal("0")) + (tax if tax is not None else Decimal("0"))
            if abs(expected_total - total) > MONEY_TOLERANCE:
                problems.append(f"item totals sum {expected_total} != total {total}")

    return bool(problems), problems


def check_line_item_extraction_warning(receipt_details: ReceiptDetails) -> tuple[bool, list[str]]:
    """Flag item-line inconsistencies that suggest extraction noise.

    Receipts print machine-generated arithmetic, so when item lines disagree
    with a consistent summary the extraction is the suspect, not the receipt.
    This is a data-quality signal: it does not feed needs_audit.
    """

    problems: list[str] = []
    for index, item in enumerate(receipt_details.items):
        problem = line_total_inconsistency(item, index)
        if problem:
            problems.append(problem)

    item_totals, every_item_total_known = known_item_totals(receipt_details)
    subtotal = parse_amount(receipt_details.subtotal)
    if every_item_total_known and subtotal is not None:
        items_sum = sum(item_totals, Decimal("0"))
        if abs(items_sum - subtotal) > MONEY_TOLERANCE:
            problems.append(f"item totals sum {items_sum} != subtotal {subtotal}")

    return bool(problems), problems


def check_item_extraction_warning(receipt_details: ReceiptDetails) -> tuple[bool, list[str]]:
    """Backward-compatible alias for the old helper name."""

    return check_line_item_extraction_warning(receipt_details)


def deterministic_checks_note(
    *,
    amount_over_limit: bool,
    math_error: bool,
    math_problems: list[str],
    line_item_extraction_warning: bool,
    warning_problems: list[str],
    receipt_details: ReceiptDetails,
) -> str:
    amount_note = f"total={receipt_details.total!r} limit={AUDIT_TOTAL_LIMIT} -> amount_over_limit={amount_over_limit}"
    math_note = (
        f"math_error={math_error}"
        + (f" ({'; '.join(math_problems)})" if math_problems else " (summary math consistent)")
    )
    warning_note = (
        f"line_item_extraction_warning={line_item_extraction_warning}"
        + (f" ({'; '.join(warning_problems)})" if warning_problems else " (item lines consistent)")
    )
    return f"Deterministic checks: {amount_note}; {math_note}; {warning_note}."


def judge_receipt_for_audit(
    receipt_details: ReceiptDetails,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> AuditJudgment:
    """Run only the LLM-judged audit fields: not_travel_related and handwritten_x."""

    settings = load_settings()
    active_client = client or get_client(settings)
    active_model = model or settings.audit_model

    return create_structured_response(
        client=active_client,
        model=active_model,
        schema_name="audit_judgment",
        schema_model=AuditJudgment,
        instructions=AUDIT_INSTRUCTIONS,
        user_content=[
            {
                "type": "input_text",
                "text": receipt_details.model_dump_json(indent=2),
            }
        ],
    )


def compose_audit_decision(receipt_details: ReceiptDetails, judgment: AuditJudgment) -> AuditDecision:
    amount_over_limit = check_amount_over_limit(receipt_details)
    math_error, math_problems = check_math_error(receipt_details)
    line_item_extraction_warning, warning_problems = check_line_item_extraction_warning(receipt_details)
    needs_audit = any(
        (
            judgment.not_travel_related,
            amount_over_limit,
            math_error,
            judgment.handwritten_x,
        )
    )
    reasoning = (
        judgment.reasoning.rstrip()
        + "\n"
        + deterministic_checks_note(
            amount_over_limit=amount_over_limit,
            math_error=math_error,
            math_problems=math_problems,
            line_item_extraction_warning=line_item_extraction_warning,
            warning_problems=warning_problems,
            receipt_details=receipt_details,
        )
    )

    return AuditDecision(
        not_travel_related=judgment.not_travel_related,
        amount_over_limit=amount_over_limit,
        math_error=math_error,
        handwritten_x=judgment.handwritten_x,
        line_item_extraction_warning=line_item_extraction_warning,
        reasoning=reasoning,
        needs_audit=needs_audit,
    )


def evaluate_receipt_for_audit(
    receipt_details: ReceiptDetails,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> AuditDecision:
    judgment = judge_receipt_for_audit(receipt_details, client=client, model=model)
    return compose_audit_decision(receipt_details, judgment)
