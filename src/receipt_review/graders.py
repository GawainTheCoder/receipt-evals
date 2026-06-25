from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from receipt_review.schemas import ReceiptDetails
from receipt_review.steps.audit import (
    check_amount_over_limit,
    check_line_item_extraction_warning,
    check_math_error,
)


JsonObject = dict[str, Any]
GradeResult = dict[str, Any]

LINE_ITEM_WARNING_FIELD = "line_item_extraction_warning"
LEGACY_LINE_ITEM_WARNING_FIELD = "item_extraction_warning"


@dataclass(frozen=True)
class GraderContext:
    reference_audit: JsonObject
    system_audit: JsonObject
    reference_extraction: JsonObject
    system_extraction: JsonObject


@dataclass(frozen=True)
class Grader:
    name: str
    comment: str
    grade: Callable[[GraderContext], GradeResult]

    def evaluate(self, context: GraderContext) -> GradeResult:
        result = self.grade(context)
        return {
            "name": self.name,
            "passed": result["passed"],
            "expected": result.get("expected"),
            "actual": result.get("actual"),
            "comment": self.comment,
            "details": result.get("details", {}),
        }


POLICY_FLAG_FIELDS = (
    "not_travel_related",
    "amount_over_limit",
    "math_error",
    "handwritten_x",
)


def audit_field_match(field_name: str) -> Callable[[GraderContext], GradeResult]:
    """Create an exact-match grader for one boolean field in the audit JSON."""

    def grade(context: GraderContext) -> GradeResult:
        expected = audit_value(context.reference_audit, field_name)
        actual = audit_value(context.system_audit, field_name)
        return {
            "passed": expected == actual,
            "expected": expected,
            "actual": actual,
        }

    return grade


def audit_value(audit: JsonObject, field_name: str) -> Any:
    if field_name == LINE_ITEM_WARNING_FIELD and field_name not in audit:
        return audit.get(LEGACY_LINE_ITEM_WARNING_FIELD)
    return audit.get(field_name)


def audit_policy_consistency(context: GraderContext) -> GradeResult:
    """Check whether the system's needs_audit value follows the v0 audit policy flags."""

    missing_fields = [
        field_name
        for field_name in (*POLICY_FLAG_FIELDS, "needs_audit")
        if field_name not in context.system_audit
    ]
    expected_needs_audit = any(bool(context.system_audit.get(field_name)) for field_name in POLICY_FLAG_FIELDS)
    actual_needs_audit = context.system_audit.get("needs_audit")

    return {
        "passed": not missing_fields and expected_needs_audit == actual_needs_audit,
        "expected": expected_needs_audit,
        "actual": actual_needs_audit,
        "details": {
            "policy_flags": {field_name: context.system_audit.get(field_name) for field_name in POLICY_FLAG_FIELDS},
            "missing_fields": missing_fields,
        },
    }


def has_explicit_handwritten_x(extraction: JsonObject) -> bool:
    """Derive whether extraction captured an explicit standalone handwritten X."""

    if extraction.get("handwritten_x_present") is not None:
        return extraction.get("handwritten_x_present") is True

    handwritten_notes = extraction.get("handwritten_notes", [])
    if not isinstance(handwritten_notes, list):
        return False
    return any(repr_note_has_standalone_x(note) for note in handwritten_notes)


def repr_note_has_standalone_x(note: Any) -> bool:
    if not isinstance(note, str):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])x(?![A-Za-z0-9])", note, flags=re.IGNORECASE))


def derived_deterministic_flags(extraction: JsonObject) -> JsonObject | None:
    """Recompute the deterministic audit flags from a raw extraction JSON.

    Returns None when the extraction is missing or does not validate as
    ReceiptDetails, so graders can fail visibly instead of crashing.
    """

    try:
        receipt_details = ReceiptDetails.model_validate(extraction)
    except ValidationError:
        return None
    math_error, math_problems = check_math_error(receipt_details)
    line_item_extraction_warning, warning_problems = check_line_item_extraction_warning(receipt_details)
    return {
        "amount_over_limit": check_amount_over_limit(receipt_details),
        "math_error": math_error,
        LINE_ITEM_WARNING_FIELD: line_item_extraction_warning,
        "problems": {
            "math_error": math_problems,
            LINE_ITEM_WARNING_FIELD: warning_problems,
        },
    }


def line_item_extraction_warning_extraction_match(context: GraderContext) -> GradeResult:
    """Check whether reference and system extractions agree on the derived line-item warning.

    Both sides run the same deterministic check, so this measures
    extraction-induced divergence, not the correctness of the check itself.
    """

    reference = derived_deterministic_flags(context.reference_extraction)
    system = derived_deterministic_flags(context.system_extraction)
    expected = reference[LINE_ITEM_WARNING_FIELD] if reference else None
    actual = system[LINE_ITEM_WARNING_FIELD] if system else None
    return {
        "passed": expected is not None and expected == actual,
        "expected": expected,
        "actual": actual,
        "details": {
            "reference_problems": reference["problems"][LINE_ITEM_WARNING_FIELD] if reference else None,
            "system_problems": system["problems"][LINE_ITEM_WARNING_FIELD] if system else None,
        },
    }


DETERMINISTIC_FLAG_FIELDS = (
    "amount_over_limit",
    "math_error",
    LINE_ITEM_WARNING_FIELD,
)


def deterministic_flags_consistency(context: GraderContext) -> GradeResult:
    """Check that the saved audit's deterministic flags match a recompute from its own extraction.

    Near-tautological on freshly generated runs; its value is catching saved
    runs and snapshots that predate or diverge from the current deterministic
    code.
    """

    derived = derived_deterministic_flags(context.system_extraction)
    expected = {field_name: derived[field_name] for field_name in DETERMINISTIC_FLAG_FIELDS} if derived else None
    actual = {field_name: audit_value(context.system_audit, field_name) for field_name in DETERMINISTIC_FLAG_FIELDS}
    return {
        "passed": expected is not None and expected == actual,
        "expected": expected,
        "actual": actual,
        "details": {"problems": derived["problems"] if derived else None},
    }


def handwritten_x_extraction_match(context: GraderContext) -> GradeResult:
    """Check whether extraction-level handwritten notes agree on explicit handwritten X presence."""

    expected = has_explicit_handwritten_x(context.reference_extraction)
    actual = has_explicit_handwritten_x(context.system_extraction)
    return {
        "passed": expected == actual,
        "expected": expected,
        "actual": actual,
        "details": {
            "reference_handwritten_notes": context.reference_extraction.get("handwritten_notes"),
            "reference_handwritten_x_present": context.reference_extraction.get("handwritten_x_present"),
            "system_handwritten_notes": context.system_extraction.get("handwritten_notes"),
            "system_handwritten_x_present": context.system_extraction.get("handwritten_x_present"),
        },
    }


# Edit graders here.
#
# Each Grader needs:
# - name: stable key that appears in saved evaluation records.
# - comment: plain-English explanation saved with every grade for visibility.
# - grade: a function that accepts GraderContext and returns:
#   {"passed": bool, "expected": ..., "actual": ..., optional "details": {...}}.
#
# To add a grader, define a small function above and add a Grader entry below.
# To remove or pause a grader, remove or comment out its entry in RECEIPT_GRADERS.
RECEIPT_GRADERS = (
    Grader(
        name="handwritten_x_extraction_match",
        comment=(
            "Passes when reference and system extraction agree on whether the receipt "
            "contains an explicit standalone handwritten X."
        ),
        grade=handwritten_x_extraction_match,
    ),
    Grader(
        name="needs_audit_match",
        comment="Passes when system needs_audit exactly matches the train reference.",
        grade=audit_field_match("needs_audit"),
    ),
    Grader(
        name="not_travel_related_match",
        comment="Passes when system not_travel_related exactly matches the train reference.",
        grade=audit_field_match("not_travel_related"),
    ),
    Grader(
        name="amount_over_limit_match",
        comment="Passes when system amount_over_limit exactly matches the train reference.",
        grade=audit_field_match("amount_over_limit"),
    ),
    Grader(
        name="math_error_match",
        comment="Passes when system math_error exactly matches the train reference.",
        grade=audit_field_match("math_error"),
    ),
    Grader(
        name="handwritten_x_match",
        comment="Passes when system handwritten_x exactly matches the train reference.",
        grade=audit_field_match("handwritten_x"),
    ),
    Grader(
        name="line_item_extraction_warning_extraction_match",
        comment=(
            "Passes when reference and system extraction agree on the derived "
            "line_item_extraction_warning (item lines inconsistent with the summary fields)."
        ),
        grade=line_item_extraction_warning_extraction_match,
    ),
    Grader(
        name="audit_policy_consistency",
        comment=(
            "Passes when the system's needs_audit equals the OR of the system policy flags: "
            "not_travel_related, amount_over_limit, math_error, and handwritten_x."
        ),
        grade=audit_policy_consistency,
    ),
    Grader(
        name="deterministic_flags_consistency",
        comment=(
            "Passes when the saved audit's deterministic flags (amount_over_limit, math_error, "
            "line_item_extraction_warning) match a recompute from the run's own extraction with current code."
        ),
        grade=deterministic_flags_consistency,
    ),
)

# Graders that need extraction JSONs, which the audit-only grade_audit path
# does not provide.
EXTRACTION_DEPENDENT_GRADERS = frozenset(
    {
        "handwritten_x_extraction_match",
        "line_item_extraction_warning_extraction_match",
        "deterministic_flags_consistency",
    }
)

AUDIT_GRADERS = tuple(
    grader
    for grader in RECEIPT_GRADERS
    if grader.name not in EXTRACTION_DEPENDENT_GRADERS
)


def grade_receipt(
    *,
    reference_audit: JsonObject,
    system_audit: JsonObject,
    reference_extraction: JsonObject | None = None,
    system_extraction: JsonObject | None = None,
) -> list[GradeResult]:
    context = GraderContext(
        reference_audit=reference_audit,
        system_audit=system_audit,
        reference_extraction=reference_extraction or {},
        system_extraction=system_extraction or {},
    )
    return [grader.evaluate(context) for grader in RECEIPT_GRADERS]


# Backward-compatible audit-only helper for older local scripts.
# New code should call grade_receipt so extraction-level graders can run too.
def grade_audit(reference_audit: JsonObject, system_audit: JsonObject) -> list[GradeResult]:
    context = GraderContext(
        reference_audit=reference_audit,
        system_audit=system_audit,
        reference_extraction={},
        system_extraction={},
    )
    return [grader.evaluate(context) for grader in AUDIT_GRADERS]
