from __future__ import annotations

import argparse
import hashlib
import json
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate audit_results JSONs from saved extraction JSONs. "
            "Extraction outputs are reused as-is, so no extraction LLM calls are made; "
            "audit judgment LLM calls are cached by extraction content."
        )
    )
    parser.add_argument("--reviews-dir", default="outputs/reviews")
    parser.add_argument("--cache-file", default="outputs/cache/audit_judgments.json")
    args = parser.parse_args()

    reviews_dir = Path(args.reviews_dir)
    extraction_dir = reviews_dir / "extraction"
    audit_dir = reviews_dir / "audit_results"
    audit_dir.mkdir(parents=True, exist_ok=True)

    extraction_paths = sorted(extraction_dir.glob("*.json"))
    if not extraction_paths:
        raise RuntimeError(f"No extraction JSONs found under {extraction_dir}.")

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
        audit_path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {audit_path} (needs_audit={decision.needs_audit})")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"\nRegenerated {len(extraction_paths)} audits with model {settings.audit_model}: "
        f"{cache_hits} judgment(s) from cache, {len(extraction_paths) - cache_hits} new LLM call(s)."
    )
    print(f"Judgment cache: {cache_path}")


if __name__ == "__main__":
    main()
