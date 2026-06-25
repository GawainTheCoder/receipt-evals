from __future__ import annotations

import re
from pathlib import Path

from openai import OpenAI

from receipt_review.config import load_settings
from receipt_review.schemas import ReceiptDetails, ReceiptItem
from receipt_review.llm.openai_client import create_structured_response, get_client, image_to_data_url


EXTRACTION_INSTRUCTIONS = """
You extract structured receipt details from a receipt image.

- Return only facts visible in the image.
- Use null for unavailable scalar values and [] for unavailable lists.
- Preserve printed money and quantity values as strings without currency symbols.
- Represent each purchased charge as its own item, including fuel lines, debit fees, service fees, discounts, and non-product charges.
- Do not include tender lines, payment method lines, authorization lines, balance due lines, or repeated final total lines as items.
- Do not include summary rows such as FUEL TOTAL, FUEL SALE, MERCH TOTAL, SUBTOTAL, TAX, TOTAL, BALANCE, CHANGE, or AMOUNT DUE as items.
- Do not duplicate a fuel purchase as both a detailed unit-price/quantity line and a fuel-sale summary line; keep the detailed line and exclude the summary line.
- For fuel receipts, quantity is usually gallons and item_price or sale_price can be the price per gallon. Do not copy gallons, pump numbers, dates, authorization numbers, unit prices, or unlabeled numbers into subtotal or tax.
- Set subtotal and tax only when those labels are explicitly visible; use null when they are not printed.
- Set total to the final amount charged or paid, not a tender line, savings line, repeated summary line, or visually similar amount.
- Transcribe handwritten notes separately from printed receipt text.
- Treat handwritten_x_present as a conservative visual flag: set it to true only when there is a clear, intentional, standalone handwritten X mark, including a circled or boxed X.
- Do not infer a handwritten X from a letter inside a handwritten word, an arrow, a crossing stroke, a signature or scribble, faint scan noise, printed tax markers, printed item flags, or printed X characters.
- Only include exactly "X" in handwritten_notes when that same clear standalone handwritten X mark is visible. Otherwise transcribe visible handwritten words and numbers normally.
- Set handwritten_x_present to false when handwritten notes or marks are visible but no clear standalone handwritten X is present; use null only when the image is too unclear to tell.
""".strip()

NON_ITEM_ROW_LABELS = {
    "APPROVED",
    "AMOUNTDUE",
    "BALANCE",
    "CHANGE",
    "CREDIT",
    "DEBIT",
    "FUELSALE",
    "FUELTOTAL",
    "MERCHTOTAL",
    "SUBTOTAL",
    "TAX",
    "TOTAL",
}


def compact_label(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def item_has_purchase_structure(item: ReceiptItem) -> bool:
    return any(value not in (None, "") for value in (item.quantity, item.item_price, item.sale_price))


def is_obvious_non_item_row(item: ReceiptItem) -> bool:
    label = compact_label(item.description)
    if item_has_purchase_structure(item):
        return False
    return label in NON_ITEM_ROW_LABELS or bool(re.fullmatch(r"PUMP\d+", label))


def normalize_extracted_receipt_details(receipt_details: ReceiptDetails) -> ReceiptDetails:
    """Drop only obvious tender/summary rows; do not repair or infer item values."""

    return receipt_details.model_copy(
        update={"items": [item for item in receipt_details.items if not is_obvious_non_item_row(item)]}
    )


def extract_receipt_details(
    image_path: str | Path,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> ReceiptDetails:
    settings = load_settings()
    active_client = client or get_client(settings)
    active_model = model or settings.extraction_model

    receipt_details = create_structured_response(
        client=active_client,
        model=active_model,
        schema_name="receipt_details",
        schema_model=ReceiptDetails,
        instructions=EXTRACTION_INSTRUCTIONS,
        user_content=[
            {
                "type": "input_text",
                "text": "Extract the receipt details from this image.",
            },
            {
                "type": "input_image",
                "image_url": image_to_data_url(image_path),
            },
        ],
    )
    return normalize_extracted_receipt_details(receipt_details)
