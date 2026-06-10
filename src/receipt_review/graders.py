from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


JsonObject = dict[str, Any]
GradeResult = dict[str, Any]


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
        expected = context.reference_audit.get(field_name)
        actual = context.system_audit.get(field_name)
        return {
            "passed": expected == actual,
            "expected": expected,
            "actual": actual,
        }

    return grade


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
    """Derive whether extraction captured an explicit standalone handwritten X note."""

    handwritten_notes = extraction.get("handwritten_notes", [])
    if not isinstance(handwritten_notes, list):
        return False
    return any(repr_note_has_standalone_x(note) for note in handwritten_notes)


def repr_note_has_standalone_x(note: Any) -> bool:
    if not isinstance(note, str):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])x(?![A-Za-z0-9])", note, flags=re.IGNORECASE))


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
            "system_handwritten_notes": context.system_extraction.get("handwritten_notes"),
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
            "Passes when reference and system extraction agree on whether handwritten_notes "
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
        name="audit_policy_consistency",
        comment=(
            "Passes when the system's needs_audit equals the OR of the system policy flags: "
            "not_travel_related, amount_over_limit, math_error, and handwritten_x."
        ),
        grade=audit_policy_consistency,
    ),
)

AUDIT_GRADERS = tuple(
    grader
    for grader in RECEIPT_GRADERS
    if grader.name != "handwritten_x_extraction_match"
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
