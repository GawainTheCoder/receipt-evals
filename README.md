# Receipt Evals

Small v0 workflow for reviewing receipt photos.

The system is intentionally two-step:

1. `extract_receipt_details(image_path)` reads one receipt image and returns structured receipt data.
2. `evaluate_receipt_for_audit(receipt_details)` decides whether the receipt should be audited.

The audit step is split between the model and plain code. The LLM judges only
`not_travel_related` and `handwritten_x`; `amount_over_limit` and `math_error`
are computed deterministically from the extracted amounts, and `needs_audit` is
the OR of the four flags (see `src/receipt_review/steps/audit.py`).

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

The optional image preflight module can use `tesseract` to detect upside-down receipts and write corrected copies to `outputs/preprocessed/`. It is currently paused so the v0 eval baseline measures extraction without preprocessing.

> **Note:** Everything under `outputs/` — saved review runs, the curated train
> references the graders compare against, evaluation reports, and the built
> comparison viewer — is intentionally untracked for now. These artifacts will
> be shared alongside part 2 of the accompanying eval-driven development blog
> series once it is close to done. Until then, running the eval scripts
> requires generating your own runs and references locally.

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
