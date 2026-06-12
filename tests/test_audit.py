from __future__ import annotations

import unittest
from decimal import Decimal

from receipt_review.schemas import AuditJudgment, Location, ReceiptDetails, ReceiptItem
from receipt_review.steps.audit import (
    check_amount_over_limit,
    check_math_error,
    compose_audit_decision,
    parse_amount,
)


def make_item(
    *,
    item_price: str | None = None,
    sale_price: str | None = None,
    quantity: str | None = "1",
    total: str | None = None,
) -> ReceiptItem:
    return ReceiptItem(
        description="Item",
        product_code=None,
        category=None,
        item_price=item_price,
        sale_price=sale_price,
        quantity=quantity,
        total=total,
    )


def make_receipt(
    *,
    items: list[ReceiptItem] | None = None,
    subtotal: str | None = None,
    tax: str | None = None,
    total: str | None = None,
) -> ReceiptDetails:
    return ReceiptDetails(
        merchant="Test Merchant",
        location=Location(city=None, state=None, zipcode=None),
        time=None,
        items=items or [],
        subtotal=subtotal,
        tax=tax,
        total=total,
        handwritten_notes=[],
    )


class DeterministicAuditChecksTest(unittest.TestCase):
    def test_parse_amount_handles_common_money_formats(self) -> None:
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("(3.00)"), Decimal("-3.00"))
        self.assertIsNone(parse_amount(""))
        self.assertIsNone(parse_amount(None))
        self.assertIsNone(parse_amount("not money"))

    def test_amount_over_limit_uses_strict_greater_than_50(self) -> None:
        self.assertFalse(check_amount_over_limit(make_receipt(total="50.00")))
        self.assertTrue(check_amount_over_limit(make_receipt(total="50.01")))
        self.assertFalse(check_amount_over_limit(make_receipt(total=None)))
        self.assertFalse(check_amount_over_limit(make_receipt(total="not money")))

    def test_math_error_is_false_for_consistent_subtotal_tax_and_total(self) -> None:
        receipt = make_receipt(
            items=[
                make_item(item_price="10.00", quantity="1", total="10.00"),
                make_item(item_price="5.00", quantity="1", total="5.00"),
            ],
            subtotal="15.00",
            tax="1.20",
            total="16.20",
        )

        has_error, problems = check_math_error(receipt)

        self.assertFalse(has_error)
        self.assertEqual(problems, [])

    def test_missing_or_unparseable_inputs_do_not_create_math_error_by_themselves(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price=None, quantity=None, total=None)],
            subtotal=None,
            tax=None,
            total="not money",
        )

        has_error, problems = check_math_error(receipt)

        self.assertFalse(has_error)
        self.assertEqual(problems, [])

    def test_math_error_flags_item_sum_mismatch(self) -> None:
        receipt = make_receipt(
            items=[
                make_item(item_price="10.00", quantity="1", total="10.00"),
                make_item(item_price="5.00", quantity="1", total="5.00"),
            ],
            subtotal="16.00",
            tax="1.00",
            total="17.00",
        )

        has_error, problems = check_math_error(receipt)

        self.assertTrue(has_error)
        self.assertTrue(any("item totals sum" in problem for problem in problems))

    def test_math_error_flags_subtotal_tax_total_mismatch(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="15.00", quantity="1", total="15.00")],
            subtotal="15.00",
            tax="1.00",
            total="15.50",
        )

        has_error, problems = check_math_error(receipt)

        self.assertTrue(has_error)
        self.assertTrue(any("subtotal" in problem and "tax" in problem for problem in problems))

    def test_line_total_check_allows_small_relative_fuel_rounding_difference(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="4.209", quantity="20.67", total="87.00")],
            subtotal=None,
            tax=None,
            total="87.00",
        )

        has_error, problems = check_math_error(receipt)

        self.assertFalse(has_error)
        self.assertEqual(problems, [])

    def test_line_total_check_flags_impossible_price_quantity_total(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="4.00", quantity="2", total="11.00")],
            subtotal=None,
            tax=None,
            total="11.00",
        )

        has_error, problems = check_math_error(receipt)

        self.assertTrue(has_error)
        self.assertTrue(any("matches no consistent reading" in problem for problem in problems))

    def test_compose_audit_decision_ors_all_policy_flags(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="50.01", quantity="1", total="50.01")],
            subtotal=None,
            tax=None,
            total="50.01",
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=False,
            reasoning="LLM judgment.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertTrue(decision.amount_over_limit)
        self.assertFalse(decision.math_error)
        self.assertTrue(decision.needs_audit)
        self.assertIn("Deterministic checks:", decision.reasoning)


if __name__ == "__main__":
    unittest.main()
