from __future__ import annotations

import argparse
import json
from pathlib import Path

from receipt_review.config import load_settings
from receipt_review.domain.schemas import ReceiptReviewResult, ReviewModels
from receipt_review.llm.openai_client import get_client
from receipt_review.steps.audit import evaluate_receipt_for_audit
from receipt_review.steps.extraction import extract_receipt_details


def review_receipt(image_path: str | Path) -> ReceiptReviewResult:
    settings = load_settings()
    client = get_client(settings)
    receipt_details = extract_receipt_details(
        image_path,
        client=client,
        model=settings.extraction_model,
    )
    audit_decision = evaluate_receipt_for_audit(
        receipt_details,
        client=client,
        model=settings.audit_model,
    )

    return ReceiptReviewResult(
        image_path=str(Path(image_path)),
        receipt_details=receipt_details,
        audit_decision=audit_decision,
        models=ReviewModels(
            extraction=settings.extraction_model,
            audit=settings.audit_model,
        ),
    )


def output_paths_for(image_path: str | Path, output_dir: str | Path = "outputs/reviews") -> tuple[Path, Path]:
    output_path = Path(output_dir)
    image_stem = Path(image_path).stem
    return (
        output_path / "extraction" / f"{image_stem}.json",
        output_path / "audit_results" / f"{image_stem}.json",
    )


def save_review_outputs(
    result: ReceiptReviewResult,
    output_dir: str | Path = "outputs/reviews",
) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    extraction_path, audit_path = output_paths_for(result.image_path, output_path)
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    extraction_path.write_text(result.receipt_details.model_dump_json(indent=2) + "\n", encoding="utf-8")
    audit_path.write_text(result.audit_decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return extraction_path, audit_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Review one receipt image.")
    parser.add_argument("image_path", help="Path to a receipt image.")
    parser.add_argument("--output-dir", default="outputs/reviews", help="Root directory for saved review outputs.")
    parser.add_argument("--no-save", action="store_true", help="Print only; do not write output JSON.")
    args = parser.parse_args()

    result = review_receipt(args.image_path)
    if not args.no_save:
        extraction_path, audit_path = save_review_outputs(result, args.output_dir)
        print(f"Wrote {extraction_path}")
        print(f"Wrote {audit_path}")
    print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
