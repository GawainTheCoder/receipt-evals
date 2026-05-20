from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXTRACTION_FIELDS = [
    ("merchant",),
    ("location", "city"),
    ("location", "state"),
    ("location", "zipcode"),
    ("time",),
    ("subtotal",),
    ("tax",),
    ("total",),
    ("handwritten_notes",),
]

AUDIT_FIELDS = [
    ("not_travel_related",),
    ("amount_over_limit",),
    ("math_error",),
    ("handwritten_x",),
    ("needs_audit",),
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def value_at(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def normalize(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, list):
        return sorted(normalize(item) for item in value)
    return value


def status(expected: Any, actual: Any) -> str:
    return "MATCH" if normalize(expected) == normalize(actual) else "DIFF "


def print_field(label: str, expected: Any, actual: Any) -> None:
    print(f"  {status(expected, actual)} {label}: expected={expected!r} actual={actual!r}")


def resolve_output_paths(path: Path) -> tuple[Path, Path, str]:
    name = path.name
    if name.endswith(".extraction.json"):
        image_stem = name.removesuffix(".extraction.json")
        return path, path.with_name(f"{image_stem}.audit.json"), image_stem
    if name.endswith(".audit.json"):
        image_stem = name.removesuffix(".audit.json")
        return path.with_name(f"{image_stem}.extraction.json"), path, image_stem
    if path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}:
        image_stem = path.stem
        output_dir = Path("outputs/reviews")
        return (
            output_dir / f"{image_stem}.extraction.json",
            output_dir / f"{image_stem}.audit.json",
            image_stem,
        )
    raise ValueError("Expected an extraction output, audit output, or source image path.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare one saved receipt review to ground truth when available.")
    parser.add_argument("path", help="Path to an extraction output, audit output, or source image.")
    parser.add_argument("--ground-truth-dir", default="data/ground_truth", help="Ground truth root directory.")
    args = parser.parse_args()

    extraction_output_path, audit_output_path, image_stem = resolve_output_paths(Path(args.path))
    ground_truth_dir = Path(args.ground_truth_dir)
    extraction_path = ground_truth_dir / "extraction" / f"{image_stem}.json"
    audit_path = ground_truth_dir / "audit_results" / f"{image_stem}.json"

    print(f"Assessment for {image_stem}")

    if extraction_path.exists():
        expected_extraction = read_json(extraction_path)
        actual_extraction = read_json(extraction_output_path)
        print("\nExtraction")
        for field_path in EXTRACTION_FIELDS:
            label = ".".join(field_path)
            print_field(label, value_at(expected_extraction, field_path), value_at(actual_extraction, field_path))
        print_field("item_count", len(expected_extraction.get("items", [])), len(actual_extraction.get("items", [])))
    else:
        print(f"\nExtraction ground truth not found: {extraction_path}")

    if audit_path.exists():
        expected_audit = read_json(audit_path)
        actual_audit = read_json(audit_output_path)
        print("\nAudit")
        for field_path in AUDIT_FIELDS:
            label = ".".join(field_path)
            print_field(label, value_at(expected_audit, field_path), value_at(actual_audit, field_path))
    else:
        print(f"\nAudit ground truth not found: {audit_path}")


if __name__ == "__main__":
    main()
