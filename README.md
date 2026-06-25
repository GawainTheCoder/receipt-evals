# Receipt Evals

Small v1 eval-driven workflow for reviewing receipt photos.

The system is intentionally two-step:

1. `extract_receipt_details(image_path)` reads one receipt image and returns structured receipt data.
2. `evaluate_receipt_for_audit(receipt_details)` decides whether the receipt should be audited.

The audit step is split between the model and plain code. The LLM judges
`not_travel_related`; `handwritten_x` is driven by the extraction-level
`handwritten_x_present` visual flag when present, with a legacy fallback to the
audit judgment for older extractions. `amount_over_limit` and `math_error` are
computed deterministically from the extracted amounts, and `needs_audit` is the
OR of the four policy flags (see `src/receipt_review/steps/audit.py`).

`math_error` covers only summary-level arithmetic (subtotal + tax vs total).
Item-line inconsistencies - duplicated lines, tax/tender lines read as items,
misread unit prices - set a separate `line_item_extraction_warning` field
instead, because on printed receipts they almost always indicate extraction
noise rather than a receipt whose math is actually wrong. The warning is a
data-quality signal and does not feed `needs_audit`.

## V1 Milestone

The current v1 milestone is captured in the tracked comparison viewer:

```text
outputs/review_viewer/index.html
```

The viewer source `audit-rerun-full-train-20260625-191148-authoritative-x`
reruns audits from the saved `full-train-extraction-20260625-191148`
extractions using the current audit contract. On the 21 train receipts it
scores:

```text
total: 186/189
needs_audit_match: 21/21
math_error_match: 21/21
handwritten_x_extraction_match: 21/21
handwritten_x_match: 21/21
audit_policy_consistency: 21/21
deterministic_flags_consistency: 21/21
line_item_extraction_warning_extraction_match: 18/21
```

The remaining misses are isolated to `line_item_extraction_warning`, which is a
data-quality signal for item extraction noise and is intentionally excluded
from `needs_audit`.

## Setup

```bash
uv sync
```

The app reads `OPENAI_API_KEY` from `.env`, `.env.local`, or `/env`.

## Project Layout

```text
src/receipt_review/
  llm/         # OpenAI client and structured-output helpers
  steps/       # The two workflow steps: extraction and audit
  image_preflight.py  # Orientation correction before extraction
  schemas.py   # Pydantic schemas and business data contracts
  workflow.py  # Composes the two steps and saves outputs
```

The optional image preflight module can use `tesseract` to detect upside-down receipts and write corrected copies to `outputs/preprocessed/`. It is currently paused so the v1 eval milestone measures extraction without preprocessing.

> **Note:** Most generated files under `outputs/` remain untracked, including
> raw saved runs, caches, and ad hoc reports. The exception is
> `outputs/review_viewer/index.html`, which is the tracked, shareable comparison
> artifact for inspecting the current eval state.

## Data Attribution

The receipt images come from the CC BY 4.0 licensed Receipt Handwriting Detection Computer Vision Project dataset published by Roboflow:
https://universe.roboflow.com/newreceipts/receipt-handwriting-detection

## Run One Receipt

```bash
uv run python scripts/run_receipt.py data/test/Gas_20240605_164059_Raven_Scan_3_jpeg.rf.e3408aa2b936afd1f1aed84fa40d454e.jpg
```

The command writes separate extraction and audit JSON files. Repeated runs are preserved with numbered filenames so model variability can be assessed:

```text
outputs/reviews/extraction/<receipt-stem>.json
outputs/reviews/extraction/<receipt-stem> (1).json
outputs/reviews/audit_results/<receipt-stem>.json
outputs/reviews/audit_results/<receipt-stem> (1).json
```

## Assess One Output

If the image has ground truth in `data/ground_truth`, compare the saved result with:

```bash
uv run python scripts/assess_receipt.py outputs/reviews/extraction/Gas_20240605_164059_Raven_Scan_3_jpeg.rf.e3408aa2b936afd1f1aed84fa40d454e.json
```

This is not the full eval framework yet. It is a lightweight inspection helper so we can understand what should be measured before formalizing metrics.

## Regenerate Audits Without Re-Extracting

After changing the audit step, refresh saved audit outputs from the existing
extraction JSONs (no extraction LLM calls; audit judgments are cached by
extraction content in `outputs/cache/audit_judgments.json`):

```bash
uv run python scripts/rerun_audits.py
```

## Compare Runs in the Viewer

Snapshot a run with `cp -R outputs/reviews outputs/runs/<label>`. By default
`scripts/build_receipt_review_viewer.py` includes `outputs/reviews` (labeled
`current`) plus every snapshot under `outputs/runs/`, and the viewer gets a
"Review source" selector to compare them side by side:

```bash
uv run python scripts/build_receipt_review_viewer.py
```

To pick sources explicitly, pass repeated `--reviews-dir` arguments in
`label=path` form.
