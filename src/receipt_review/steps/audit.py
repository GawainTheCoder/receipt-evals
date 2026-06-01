from __future__ import annotations

from openai import OpenAI

from receipt_review.config import load_settings
from receipt_review.schemas import AuditDecision, ReceiptDetails
from receipt_review.llm.openai_client import create_structured_response, get_client


AUDIT_INSTRUCTIONS = """
You decide whether a receipt should be sent to human audit.

Use this v0 audit policy:
- Travel-related expenses include gas, fuel, hotel, airfare, and car rental. If the receipt shows gas or fuel, not_travel_related is false.
- not_travel_related is true when the merchant and purchased items are not travel-related.
- amount_over_limit is true when the final total is greater than 50.00.
- math_error is true when item totals, subtotal, tax, or final total appear inconsistent.
- handwritten_x is true when handwritten_notes contains an explicit handwritten "X".
- needs_audit is true if any policy flag is true.

Give concise reasoning grounded only in the structured receipt details provided.
""".strip()


def evaluate_receipt_for_audit(
    receipt_details: ReceiptDetails,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> AuditDecision:
    settings = load_settings()
    active_client = client or get_client(settings)
    active_model = model or settings.audit_model

    return create_structured_response(
        client=active_client,
        model=active_model,
        schema_name="audit_decision",
        schema_model=AuditDecision,
        instructions=AUDIT_INSTRUCTIONS,
        user_content=[
            {
                "type": "input_text",
                "text": receipt_details.model_dump_json(indent=2),
            }
        ],
    )
