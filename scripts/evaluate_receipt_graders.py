from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from receipt_review.graders import RECEIPT_GRADERS, grade_receipt


JsonObject = dict[str, Any]


def read_json(path: Path) -> JsonObject:
    return json.loads(path.read_text(encoding="utf-8"))


def base_receipt_stem(stem: str) -> str:
    return re.sub(r" \(\d+\)$", "", stem)


def report_path(output_dir: Path, suffix: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return output_dir / f"audit-grader-evaluation-{timestamp}.{suffix}"


def resolve_reference_stems(target: str | None, reference_dir: Path) -> list[str]:
    if target:
        path = Path(target)
        if path.suffix.casefold() in {".json", ".jpg", ".jpeg", ".png", ".webp"}:
            return [base_receipt_stem(path.stem)]
        return [base_receipt_stem(target)]
    return sorted(path.stem for path in (reference_dir / "audit_results").glob("*.json"))


def matching_run_stems(reference_stem: str, reviews_dir: Path) -> list[str]:
    audit_dir = reviews_dir / "audit_results"
    return [
        path.stem
        for path in sorted(audit_dir.glob(f"{reference_stem}*.json"))
        if base_receipt_stem(path.stem) == reference_stem
    ]


def resolve_run_stems(target: str | None, reference_stem: str, reviews_dir: Path) -> list[str]:
    target_path = Path(target) if target else None
    if (
        target_path
        and target_path.suffix.casefold() == ".json"
        and target_path.parent.name in {"extraction", "audit_results"}
    ):
        return [target_path.stem]
    return matching_run_stems(reference_stem, reviews_dir)


def comparison_paths(
    reference_stem: str,
    run_stem: str,
    reference_dir: Path,
    reviews_dir: Path,
) -> dict[str, Path]:
    paths = {
        "reference_audit": reference_dir / "audit_results" / f"{reference_stem}.json",
        "reference_extraction": reference_dir / "extraction" / f"{reference_stem}.json",
        "system_audit": reviews_dir / "audit_results" / f"{run_stem}.json",
        "system_extraction": reviews_dir / "extraction" / f"{run_stem}.json",
    }
    required_paths = ("reference_audit", "reference_extraction", "system_audit", "system_extraction")
    for path_name in required_paths:
        if not paths[path_name].exists():
            raise FileNotFoundError(f"Required evaluation file not found: {paths[path_name]}")
    return paths


def build_evaluation_record(
    *,
    evaluated_at: str,
    reference_stem: str,
    run_stem: str,
    reference_dir: Path,
    reviews_dir: Path,
) -> JsonObject:
    paths = comparison_paths(reference_stem, run_stem, reference_dir, reviews_dir)
    grades = grade_receipt(
        reference_audit=read_json(paths["reference_audit"]),
        system_audit=read_json(paths["system_audit"]),
        reference_extraction=read_json(paths["reference_extraction"]),
        system_extraction=read_json(paths["system_extraction"]),
    )
    passed = sum(1 for grade in grades if grade["passed"])
    total = len(grades)

    return {
        "schema_version": 1,
        "evaluated_at": evaluated_at,
        "receipt_stem": reference_stem,
        "run_stem": run_stem,
        "reference": {
            "audit_path": paths["reference_audit"].as_posix(),
            "extraction_path": paths["reference_extraction"].as_posix()
            if paths["reference_extraction"].exists()
            else None,
        },
        "system": {
            "audit_path": paths["system_audit"].as_posix(),
            "extraction_path": paths["system_extraction"].as_posix()
            if paths["system_extraction"].exists()
            else None,
        },
        "grades": grades,
        "score": {
            "passed": passed,
            "total": total,
            "all_passed": passed == total,
        },
    }


def validate_targets(
    stems: list[str],
    target: str | None,
    reference_dir: Path,
    reviews_dir: Path,
) -> list[tuple[str, list[str]]]:
    targets = []
    for reference_stem in stems:
        run_stems = resolve_run_stems(target, reference_stem, reviews_dir)
        if not run_stems:
            raise FileNotFoundError(f"No saved system audit runs found for {reference_stem!r} under {reviews_dir}.")
        for run_stem in run_stems:
            comparison_paths(reference_stem, run_stem, reference_dir, reviews_dir)
        targets.append((reference_stem, run_stems))
    return targets


def summarize(records: list[JsonObject], evaluated_at: str) -> JsonObject:
    grader_totals = {
        grader.name: {
            "passed": 0,
            "failed": 0,
            "total": 0,
            "comment": grader.comment,
        }
        for grader in RECEIPT_GRADERS
    }

    for record in records:
        for grade in record["grades"]:
            totals = grader_totals[grade["name"]]
            totals["total"] += 1
            if grade["passed"]:
                totals["passed"] += 1
            else:
                totals["failed"] += 1
    for totals in grader_totals.values():
        totals["pass_percentage"] = round((totals["passed"] / totals["total"]) * 100, 1) if totals["total"] else 0.0

    passed = sum(record["score"]["passed"] for record in records)
    total = sum(record["score"]["total"] for record in records)
    return {
        "schema_version": 1,
        "evaluated_at": evaluated_at,
        "record_count": len(records),
        "score": {
            "passed": passed,
            "total": total,
            "all_passed": passed == total,
        },
        "grader_totals": grader_totals,
    }


def print_summary(records: list[JsonObject], summary: JsonObject, jsonl_path: Path, summary_path: Path) -> None:
    print("AUDIT GRADER EVALUATION")
    print(f"Records: {summary['record_count']}")
    print(f"Overall: {summary['score']['passed']}/{summary['score']['total']} passed")
    print(f"Wrote: {jsonl_path}")
    print(f"Wrote: {summary_path}")

    print("\nBy grader")
    for grader_name, totals in summary["grader_totals"].items():
        print(f"  {grader_name}: {totals['passed']}/{totals['total']} passed ({totals['pass_percentage']}%)")

    print("\nBy receipt run")
    for record in records:
        failed_names = [grade["name"] for grade in record["grades"] if not grade["passed"]]
        status = "PASS" if record["score"]["all_passed"] else "FAIL"
        failed_suffix = "" if not failed_names else f" failed={','.join(failed_names)}"
        print(
            f"  [{status}] {record['receipt_stem']} :: {record['run_stem']} "
            f"{record['score']['passed']}/{record['score']['total']} passed{failed_suffix}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create simple audit-grader evaluation records for saved receipt review runs."
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Optional source image, saved JSON path, or receipt stem. Omit to evaluate every train reference run.",
    )
    parser.add_argument("--reference-dir", default="outputs/train_reference")
    parser.add_argument("--reviews-dir", default="outputs/reviews")
    parser.add_argument("--output-dir", default="outputs/evaluations")
    args = parser.parse_args()

    reference_dir = Path(args.reference_dir)
    reviews_dir = Path(args.reviews_dir)
    output_dir = Path(args.output_dir)

    stems = resolve_reference_stems(args.target, reference_dir)
    if not stems:
        raise RuntimeError(f"No train reference audits found under {reference_dir / 'audit_results'}.")

    # Validate every required input before writing output so a failed run does
    # not leave a partial evaluation file behind.
    targets = validate_targets(stems, args.target, reference_dir, reviews_dir)

    evaluated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    records = [
        build_evaluation_record(
            evaluated_at=evaluated_at,
            reference_stem=reference_stem,
            run_stem=run_stem,
            reference_dir=reference_dir,
            reviews_dir=reviews_dir,
        )
        for reference_stem, run_stems in targets
        for run_stem in run_stems
    ]
    summary = summarize(records, evaluated_at)

    jsonl_path = report_path(output_dir, "jsonl")
    summary_path = jsonl_path.with_suffix(".summary.json")
    with jsonl_path.open("w", encoding="utf-8") as jsonl_file:
        for record in records:
            jsonl_file.write(json.dumps(record, sort_keys=True) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print_summary(records, summary, jsonl_path, summary_path)
    raise SystemExit(0 if summary["score"]["all_passed"] else 1)


if __name__ == "__main__":
    main()
