from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from receipt_review.config import load_settings
from receipt_review.schemas import ReceiptDetails
from receipt_review.llm.openai_client import create_structured_response, get_client, image_to_data_url


EXTRACTION_INSTRUCTIONS = """
You extract structured receipt details from a receipt image.

- Return only facts visible in the image.
- Use null for unavailable scalar values and [] for unavailable lists.
- Preserve printed money and quantity values as strings without currency symbols.
- Represent each purchased charge as its own item, including fuel lines, debit fees, service fees, discounts, and non-product charges.
- Do not include tender lines, payment method lines, authorization lines, balance due lines, or repeated final total lines as items.
- Do not duplicate a fuel purchase as both a detailed unit-price/quantity line and a fuel-sale summary line; keep the detailed line.
- Transcribe handwritten notes separately from printed receipt text.
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

    return create_structured_response(
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
