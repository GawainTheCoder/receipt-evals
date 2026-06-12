from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from receipt_review.config import load_settings
from receipt_review.llm.openai_client import get_client
from receipt_review.schemas import AuditJudgment, ReceiptDetails
from receipt_review.steps.audit import AUDIT_INSTRUCTIONS, compose_audit_decision, judge_receipt_for_audit


def judgment_cache_key(model: str, receipt_details: ReceiptDetails) -> str:
    payload = "\n".join(
        (
            model,
            AUDIT_INSTRUCTIONS,
            json.dumps(receipt_details.model_dump(mode="json"), sort_keys=True),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_cache(cache_path: Path) -> dict[str, dict]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def default_output_dir() -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    return Path("outputs/runs") / f"audit-rerun-{timestamp}"


def assert_output_paths_are_clear(extraction_paths: list[Path], output_dir: Path) -> None:
    existing_paths = [
        target_path
        for extraction_path in extraction_paths
        for target_path in (
            output_dir / "extraction" / extraction_path.name,
            output_dir / "audit_results" / extraction_path.name,
        )
        if target_path.exists()
    ]
    if existing_paths:
        formatted_paths = "\n".join(f"  {path}" for path in existing_paths[:10])
        extra_count = len(existing_paths) - 10
        suffix = f"\n  ...and {extra_count} more" if extra_count > 0 else ""
        raise FileExistsError(
            "Refusing to overwrite existing rerun outputs. "
            "Choose a new --output-dir or pass --in-place to overwrite current audit files.\n"
            f"{formatted_paths}{suffix}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate audit_results JSONs from saved extraction JSONs. "
            "Extraction outputs are reused as-is, so no extraction LLM calls are made; "
            "audit judgment LLM calls are cached by extraction content."
        )
    )
    parser.add_argument("--reviews-dir", default="outputs/reviews")
    parser.add_argument(
        "--output-dir",
        help=(
            "Root directory for a regenerated review snapshot. Defaults to "
            "outputs/runs/audit-rerun-<timestamp>. Cannot be used with --in-place."
        ),
    )
    parser.add_argument("--cache-file", default="outputs/cache/audit_judgments.json")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite audit JSONs under --reviews-dir/audit_results instead of writing a preserved snapshot.",
    )
    args = parser.parse_args()
    if args.in_place and args.output_dir:
        parser.error("--output-dir cannot be used with --in-place.")

    reviews_dir = Path(args.reviews_dir)
    extraction_dir = reviews_dir / "extraction"

    extraction_paths = sorted(extraction_dir.glob("*.json"))
    if not extraction_paths:
        raise RuntimeError(f"No extraction JSONs found under {extraction_dir}.")

    output_dir = reviews_dir if args.in_place else Path(args.output_dir) if args.output_dir else default_output_dir()
    output_extraction_dir = output_dir / "extraction"
    audit_dir = output_dir / "audit_results"
    if not args.in_place:
        assert_output_paths_are_clear(extraction_paths, output_dir)
        output_extraction_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings()
    client = get_client(settings)
    cache_path = Path(args.cache_file)
    cache = load_cache(cache_path)
    cache_hits = 0

    for extraction_path in extraction_paths:
        receipt_details = ReceiptDetails.model_validate_json(extraction_path.read_text(encoding="utf-8"))
        cache_key = judgment_cache_key(settings.audit_model, receipt_details)
        cached_judgment = cache.get(cache_key)
        if cached_judgment is not None:
            judgment = AuditJudgment.model_validate(cached_judgment)
            cache_hits += 1
        else:
            judgment = judge_receipt_for_audit(receipt_details, client=client, model=settings.audit_model)
            cache[cache_key] = judgment.model_dump(mode="json")

        decision = compose_audit_decision(receipt_details, judgment)
        audit_path = audit_dir / extraction_path.name
        if not args.in_place:
            shutil.copy2(extraction_path, output_extraction_dir / extraction_path.name)
        audit_path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {audit_path} (needs_audit={decision.needs_audit})")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"\nRegenerated {len(extraction_paths)} audits with model {settings.audit_model}: "
        f"{cache_hits} judgment(s) from cache, {len(extraction_paths) - cache_hits} new LLM call(s)."
    )
    print(f"Judgment cache: {cache_path}")
    if args.in_place:
        print(f"Updated audit files in place: {audit_dir}")
    else:
        print(f"Review snapshot: {output_dir}")


if __name__ == "__main__":
    main()
