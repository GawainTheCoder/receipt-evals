from __future__ import annotations

import unittest

from receipt_review.schemas import Location, ReceiptDetails, ReceiptItem
from receipt_review.steps.extraction import normalize_extracted_receipt_details


def make_item(
    description: str,
    *,
    item_price: str | None = None,
    sale_price: str | None = None,
    quantity: str | None = None,
    total: str | None = None,
) -> ReceiptItem:
    return ReceiptItem(
        description=description,
        product_code=None,
        category=None,
        item_price=item_price,
        sale_price=sale_price,
        quantity=quantity,
        total=total,
    )


def make_receipt(items: list[ReceiptItem]) -> ReceiptDetails:
    return ReceiptDetails(
        merchant="Test Merchant",
        location=Location(city=None, state=None, zipcode=None),
        time=None,
        items=items,
        subtotal=None,
        tax=None,
        total="50.33",
        handwritten_notes=[],
    )


class ExtractionNormalizationTest(unittest.TestCase):
    def test_removes_only_obvious_non_item_rows_without_repairing_fuel_line(self) -> None:
        receipt = make_receipt(
            [
                make_item("REGULAR PRICE/ GALLON", item_price="2.4601", quantity="12.4601", total="30.039"),
                make_item("PUMP#2", total="30.33"),
                make_item("DEBIT", total="50.33"),
                make_item("TOTAL", total="50.33"),
            ]
        )

        normalized = normalize_extracted_receipt_details(receipt)

        self.assertEqual(len(normalized.items), 1)
        self.assertEqual(normalized.items[0].description, "REGULAR PRICE/ GALLON")
        self.assertEqual(normalized.items[0].item_price, "2.4601")
        self.assertEqual(normalized.items[0].total, "30.039")

    def test_preserves_structured_rows_and_fee_like_descriptions(self) -> None:
        receipt = make_receipt(
            [
                make_item("REGULAR", item_price="3.469", quantity="12.433", total="43.13"),
                make_item("DEBIT FEE", total="0.35"),
                make_item("TOTAL", quantity="1", total="43.13"),
            ]
        )

        normalized = normalize_extracted_receipt_details(receipt)

        self.assertEqual([item.description for item in normalized.items], ["REGULAR", "DEBIT FEE", "TOTAL"])


if __name__ == "__main__":
    unittest.main()
