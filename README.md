# Receipt Evals

Small v0 workflow for reviewing receipt photos.

The system is intentionally two-step:

1. `extract_receipt_details(image_path)` reads one receipt image and returns structured receipt data.
2. `evaluate_receipt_for_audit(receipt_details)` decides whether the receipt should be audited.

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

## Data Attribution

The receipt images come from the CC BY 4.0 licensed Receipt Handwriting Detection Computer Vision Project dataset published by Roboflow:
https://universe.roboflow.com/newreceipts/receipt-handwriting-detection

## Run One Receipt

```bash
uv run python scripts/run_receipt.py data/test/Gas_20240605_164059_Raven_Scan_3_jpeg.rf.e3408aa2b936afd1f1aed84fa40d454e.jpg
```

The command writes separate extraction and audit JSON files:

```text
outputs/reviews/extraction/<receipt-stem>.json
outputs/reviews/audit_results/<receipt-stem>.json
```

## Assess One Output

If the image has ground truth in `data/ground_truth`, compare the saved result with:

```bash
uv run python scripts/assess_receipt.py outputs/reviews/extraction/Gas_20240605_164059_Raven_Scan_3_jpeg.rf.e3408aa2b936afd1f1aed84fa40d454e.json
```

This is not the full eval framework yet. It is a lightweight inspection helper so we can understand what should be measured before formalizing metrics.
