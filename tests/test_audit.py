from __future__ import annotations

import unittest
from decimal import Decimal

from receipt_review.schemas import AuditDecision, AuditJudgment, Location, ReceiptDetails, ReceiptItem
from receipt_review.graders import has_explicit_handwritten_x
from receipt_review.steps.audit import (
    check_amount_over_limit,
    check_item_extraction_warning,
    check_line_item_extraction_warning,
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


HANDWRITTEN_X_PRESENT_UNSET = object()


def make_receipt(
    *,
    items: list[ReceiptItem] | None = None,
    subtotal: str | None = None,
    tax: str | None = None,
    total: str | None = None,
    handwritten_notes: list[str] | None = None,
    handwritten_x_present: bool | None | object = HANDWRITTEN_X_PRESENT_UNSET,
) -> ReceiptDetails:
    receipt_payload = {
        "merchant": "Test Merchant",
        "location": Location(city=None, state=None, zipcode=None),
        "time": None,
        "items": items or [],
        "subtotal": subtotal,
        "tax": tax,
        "total": total,
        "handwritten_notes": handwritten_notes or [],
    }
    if handwritten_x_present is not HANDWRITTEN_X_PRESENT_UNSET:
        receipt_payload["handwritten_x_present"] = handwritten_x_present
    return ReceiptDetails(**receipt_payload)


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

    def test_item_sum_mismatch_with_consistent_summary_is_warning_not_math_error(self) -> None:
        receipt = make_receipt(
            items=[
                make_item(item_price="10.00", quantity="1", total="10.00"),
                make_item(item_price="5.00", quantity="1", total="5.00"),
            ],
            subtotal="16.00",
            tax="1.00",
            total="17.00",
        )

        has_error, math_problems = check_math_error(receipt)
        has_warning, warning_problems = check_line_item_extraction_warning(receipt)

        self.assertFalse(has_error)
        self.assertEqual(math_problems, [])
        self.assertTrue(has_warning)
        self.assertTrue(any("item totals sum" in problem for problem in warning_problems))

    def test_legacy_item_extraction_warning_helper_alias_still_works(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="4.00", quantity="2", total="11.00")],
            subtotal=None,
            tax=None,
            total="11.00",
        )

        self.assertEqual(
            check_item_extraction_warning(receipt),
            check_line_item_extraction_warning(receipt),
        )

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

    def test_impossible_price_quantity_total_is_warning_not_math_error(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="4.00", quantity="2", total="11.00")],
            subtotal=None,
            tax=None,
            total="11.00",
        )

        has_error, math_problems = check_math_error(receipt)
        has_warning, warning_problems = check_line_item_extraction_warning(receipt)

        self.assertFalse(has_error)
        self.assertEqual(math_problems, [])
        self.assertTrue(has_warning)
        self.assertTrue(any("matches no consistent reading" in problem for problem in warning_problems))

    def test_math_error_flags_summary_mismatch_even_when_items_are_consistent(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="15.00", quantity="1", total="15.00")],
            subtotal="15.00",
            tax="1.00",
            total="15.50",
        )

        has_warning, warning_problems = check_line_item_extraction_warning(receipt)

        self.assertFalse(has_warning)
        self.assertEqual(warning_problems, [])

    def test_item_total_mismatch_without_summary_is_not_math_error(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="10.00", quantity="1", total="10.00")],
            subtotal=None,
            tax="1.00",
            total="20.00",
        )

        has_error, problems = check_math_error(receipt)

        self.assertFalse(has_error)
        self.assertEqual(problems, [])

    def test_item_reconciliation_without_summary_does_not_drive_needs_audit(self) -> None:
        receipt = make_receipt(
            items=[make_item(item_price="10.00", quantity="1", total="10.00")],
            subtotal=None,
            tax="1.00",
            total="20.00",
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=False,
            reasoning="LLM judgment.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertFalse(decision.math_error)
        self.assertFalse(decision.needs_audit)
        self.assertIn("summary math not checked", decision.reasoning)

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

    def test_handwritten_x_present_feeds_needs_audit_when_judgment_misses_it(self) -> None:
        receipt = make_receipt(
            total="43.13",
            handwritten_notes=["559139", "Tundra", "Monterey"],
            handwritten_x_present=True,
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=False,
            reasoning="LLM judgment missed the extracted mark.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertTrue(decision.handwritten_x)
        self.assertTrue(decision.needs_audit)

    def test_handwritten_x_present_null_blocks_notes_fallback(self) -> None:
        receipt = make_receipt(
            total="10.00",
            handwritten_notes=["X"],
            handwritten_x_present=None,
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=True,
            reasoning="Legacy note fallback saw an X.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertFalse(decision.handwritten_x)
        self.assertFalse(decision.needs_audit)

    def test_legacy_receipt_without_handwritten_x_present_uses_judgment(self) -> None:
        receipt = make_receipt(
            total="10.00",
            handwritten_notes=["X"],
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=True,
            reasoning="Legacy note fallback saw an X.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertTrue(decision.handwritten_x)
        self.assertTrue(decision.needs_audit)

    def test_line_item_extraction_warning_does_not_feed_needs_audit(self) -> None:
        receipt = make_receipt(
            items=[
                make_item(item_price="10.00", quantity="1", total="10.00"),
                make_item(item_price="10.00", quantity="1", total="10.00"),
            ],
            subtotal="10.00",
            tax="0.80",
            total="10.80",
        )
        judgment = AuditJudgment(
            not_travel_related=False,
            handwritten_x=False,
            reasoning="LLM judgment.",
        )

        decision = compose_audit_decision(receipt, judgment)

        self.assertTrue(decision.line_item_extraction_warning)
        self.assertFalse(decision.math_error)
        self.assertFalse(decision.needs_audit)
        self.assertIn("line_item_extraction_warning=True", decision.reasoning)

    def test_audit_decision_accepts_legacy_item_extraction_warning_key(self) -> None:
        decision = AuditDecision.model_validate(
            {
                "not_travel_related": False,
                "amount_over_limit": False,
                "math_error": False,
                "handwritten_x": False,
                "item_extraction_warning": True,
                "reasoning": "legacy saved audit",
                "needs_audit": False,
            }
        )

        self.assertTrue(decision.line_item_extraction_warning)
        self.assertIn("line_item_extraction_warning", decision.model_dump())
        self.assertNotIn("item_extraction_warning", decision.model_dump())

    def test_receipt_details_accepts_legacy_extraction_without_handwritten_x_present(self) -> None:
        receipt = ReceiptDetails.model_validate(
            {
                "merchant": "Test Merchant",
                "location": {"city": None, "state": None, "zipcode": None},
                "time": None,
                "items": [],
                "subtotal": None,
                "tax": None,
                "total": "10.00",
                "handwritten_notes": ["not an x"],
            }
        )

        self.assertIsNone(receipt.handwritten_x_present)

    def test_has_explicit_handwritten_x_uses_structured_field_or_notes(self) -> None:
        self.assertTrue(has_explicit_handwritten_x({"handwritten_x_present": True, "handwritten_notes": []}))
        self.assertTrue(has_explicit_handwritten_x({"handwritten_notes": ["X"]}))
        self.assertFalse(has_explicit_handwritten_x({"handwritten_x_present": None, "handwritten_notes": ["X"]}))
        self.assertFalse(has_explicit_handwritten_x({"handwritten_x_present": False, "handwritten_notes": ["Nissan"]}))
        self.assertFalse(has_explicit_handwritten_x({"handwritten_x_present": False, "handwritten_notes": ["X"]}))


if __name__ == "__main__":
    unittest.main()
