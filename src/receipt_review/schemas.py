from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Location(StrictModel):
    city: str | None
    state: str | None
    zipcode: str | None


class ReceiptItem(StrictModel):
    description: str | None
    product_code: str | None
    category: str | None
    item_price: str | None
    sale_price: str | None
    quantity: str | None
    total: str | None


class ReceiptDetails(StrictModel):
    merchant: str | None
    location: Location
    time: str | None
    items: list[ReceiptItem]
    subtotal: str | None
    tax: str | None
    total: str | None
    handwritten_notes: list[str]
    handwritten_x_present: bool | None = None


class AuditJudgment(StrictModel):
    """The subset of audit fields that require LLM judgment.

    amount_over_limit, math_error, and needs_audit are computed
    deterministically in steps/audit.py, not by the model.
    """

    not_travel_related: bool
    handwritten_x: bool
    reasoning: str


class AuditDecision(StrictModel):
    not_travel_related: bool
    amount_over_limit: bool
    math_error: bool
    handwritten_x: bool
    # Data-quality signal, not a policy flag: extracted item lines disagree
    # with a consistent summary. Intentionally excluded from needs_audit.
    line_item_extraction_warning: bool = Field(
        validation_alias=AliasChoices(
            "line_item_extraction_warning",
            "item_extraction_warning",
        )
    )
    reasoning: str
    needs_audit: bool


class ReviewModels(StrictModel):
    extraction: str
    audit: str


class ReceiptReviewResult(StrictModel):
    image_path: str
    receipt_details: ReceiptDetails
    audit_decision: AuditDecision
    models: ReviewModels
