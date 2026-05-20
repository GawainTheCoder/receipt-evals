from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from receipt_review.config import load_settings
from receipt_review.domain.schemas import ReceiptDetails, ReceiptItem
from receipt_review.llm.openai_client import create_structured_response, get_client, image_to_data_url


EXTRACTION_INSTRUCTIONS = """
You extract structured receipt details from a receipt image.

Return only facts visible in the image. Use null for unavailable scalar values and [] for unavailable lists.
Preserve printed money and quantity values as strings without currency symbols.
Represent each purchased charge as its own item, including fuel lines, debit fees, service fees, discounts, and non-product charges.
Do not include tender lines, payment method lines, authorization lines, balance due lines, or repeated final total lines as items.
Do not duplicate a fuel purchase as both a detailed unit-price/quantity line and a fuel-sale summary line; keep the detailed line.
Transcribe handwritten notes separately from printed receipt text.
Do not add printed receipt numbers, authorization codes, or register IDs to handwritten_notes unless they are clearly handwritten.
If a handwritten annotation is an arrow, route, vehicle label, location label, number, or X mark, include it in handwritten_notes.
Common handwritten labels in this dataset include sequoia, tundra, nissan, yos, home, vista, oakhurst, and route notes like vista->yos.
""".strip()


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

    return receipt_details.model_copy(update={"items": _clean_items(receipt_details)})


def _clean_items(receipt_details: ReceiptDetails) -> list[ReceiptItem]:
    return [
        item
        for item in receipt_details.items
        if not _is_payment_summary_item(item, receipt_details.total)
    ]


def _is_payment_summary_item(item: ReceiptItem, receipt_total: str | None) -> bool:
    if not item.description or not item.total or not receipt_total:
        return False

    payment_descriptions = {
        "amex",
        "cash",
        "credit",
        "debit",
        "discover",
        "mastercard",
        "tender",
        "visa",
    }
    normalized_description = item.description.strip().casefold()
    normalized_total = item.total.strip()
    normalized_receipt_total = receipt_total.strip()
    return (
        normalized_description in payment_descriptions
        and normalized_total == normalized_receipt_total
    )
