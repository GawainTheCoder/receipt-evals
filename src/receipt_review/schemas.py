from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(StrictModel):
    city: str | None = Field(description="City printed on the receipt, or null if unavailable.")
    state: str | None = Field(description="State or province printed on the receipt, or null if unavailable.")
    zipcode: str | None = Field(description="Postal code printed on the receipt, or null if unavailable.")


class ReceiptItem(StrictModel):
    description: str | None = Field(description="Receipt line item description.")
    product_code: str | None = Field(description="SKU, UPC, pump number, or product code, or null.")
    category: str | None = Field(description="Simple category such as Fuel, Food, Supplies, Hardware, Fee, or Other.")
    item_price: str | None = Field(description="Unit price as printed, without currency symbol, or null.")
    sale_price: str | None = Field(description="Sale or discounted unit price as printed, or null.")
    quantity: str | None = Field(description="Quantity, gallons, or units as printed, or null.")
    total: str | None = Field(description="Line total as printed, without currency symbol, or null.")


class ReceiptDetails(StrictModel):
    merchant: str | None = Field(description="Merchant name printed on the receipt.")
    location: Location = Field(description="Merchant location printed on the receipt.")
    time: str | None = Field(description="Receipt date/time as ISO-8601 if possible, or null.")
    items: list[ReceiptItem] = Field(description="Purchased items or services.")
    subtotal: str | None = Field(description="Subtotal as printed, without currency symbol, or null.")
    tax: str | None = Field(description="Tax as printed, without currency symbol, or null.")
    total: str | None = Field(description="Final total as printed, without currency symbol, or null.")
    handwritten_notes: list[str] = Field(description="Handwritten notes, annotations, arrows, X marks, or labels visible on the image.")


class AuditDecision(StrictModel):
    not_travel_related: bool = Field(description="True when the receipt does not appear travel-related.")
    amount_over_limit: bool = Field(description="True when the receipt total exceeds the v0 approval limit.")
    math_error: bool = Field(description="True when item totals, subtotal, tax, or final total appear inconsistent.")
    handwritten_x: bool = Field(description="True when handwritten notes include an X or crossed-out annotation.")
    reasoning: str = Field(description="Brief explanation of the audit decision.")
    needs_audit: bool = Field(description="True when this receipt should be sent to human audit or QA.")


class ReviewModels(StrictModel):
    extraction: str
    audit: str


class ReceiptReviewResult(StrictModel):
    image_path: str
    receipt_details: ReceiptDetails
    audit_decision: AuditDecision
    models: ReviewModels
