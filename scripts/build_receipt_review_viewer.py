from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from receipt_review.graders import RECEIPT_GRADERS, grade_receipt


IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
LINE_ITEM_WARNING_FIELD = "line_item_extraction_warning"
LEGACY_LINE_ITEM_WARNING_FIELD = "item_extraction_warning"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_audit_fields(audit: dict[str, Any] | None) -> dict[str, Any] | None:
    if audit is None:
        return None
    normalized = dict(audit)
    if LINE_ITEM_WARNING_FIELD not in normalized and LEGACY_LINE_ITEM_WARNING_FIELD in normalized:
        normalized[LINE_ITEM_WARNING_FIELD] = normalized[LEGACY_LINE_ITEM_WARNING_FIELD]
    if isinstance(normalized.get("reasoning"), str):
        normalized["reasoning"] = re.sub(
            rf"(?<![A-Za-z0-9_]){LEGACY_LINE_ITEM_WARNING_FIELD}(?![A-Za-z0-9_])",
            LINE_ITEM_WARNING_FIELD,
            normalized["reasoning"],
        )
    normalized.pop(LEGACY_LINE_ITEM_WARNING_FIELD, None)
    return normalized


def base_receipt_stem(stem: str) -> str:
    return re.sub(r" \(\d+\)$", "", stem)


def relative_path(path: Path, output_file: Path) -> str:
    return Path(os.path.relpath(path.resolve(), output_file.parent.resolve())).as_posix()


def find_receipt_image(stem: str, image_dirs: list[Path], output_file: Path) -> str | None:
    for image_dir in image_dirs:
        for extension in IMAGE_EXTENSIONS:
            candidate = image_dir / f"{stem}{extension}"
            if candidate.exists():
                return relative_path(candidate, output_file)
    return None


def score_grades(grades: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for grade in grades if grade["passed"])
    total = len(grades)
    return {
        "passed": passed,
        "total": total,
        "allPassed": passed == total,
        "passPercentage": round((passed / total) * 100, 1) if total else 0.0,
    }


def parse_review_source(value: str) -> tuple[str, Path]:
    """Parse a --reviews-dir value of the form 'label=path' or plain 'path'."""

    label, separator, path = value.partition("=")
    if separator and label and "/" not in label:
        return label, Path(path)
    return value, Path(value)


def default_review_sources(runs_dir: Path = Path("outputs/runs")) -> list[tuple[str, Path]]:
    """Current reviews plus every saved run snapshot under outputs/runs/."""

    sources = [("current", Path("outputs/reviews"))]
    if runs_dir.is_dir():
        for snapshot_dir in sorted(runs_dir.iterdir()):
            if (snapshot_dir / "audit_results").is_dir():
                sources.append((snapshot_dir.name, snapshot_dir))
    return sources


def matching_review_runs(
    stem: str,
    source_label: str,
    reviews_dir: Path,
    reference_audit: dict[str, Any] | None,
    reference_extraction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    extraction_dir = reviews_dir / "extraction"
    audit_dir = reviews_dir / "audit_results"
    runs = []

    for extraction_path in sorted(extraction_dir.glob(f"{stem}*.json")):
        if base_receipt_stem(extraction_path.stem) != stem:
            continue

        audit_path = audit_dir / extraction_path.name
        system_extraction = read_json(extraction_path)
        system_audit = normalize_audit_fields(read_json(audit_path)) if audit_path.exists() else None
        grades = (
            grade_receipt(
                reference_audit=reference_audit,
                system_audit=system_audit,
                reference_extraction=reference_extraction,
                system_extraction=system_extraction,
            )
            if reference_audit and reference_extraction and system_audit
            else []
        )
        runs.append(
            {
                "stem": extraction_path.stem,
                "source": source_label,
                "label": extraction_path.name,
                "copyNumber": copy_number_from_stem(extraction_path.stem),
                "extractionPath": extraction_path.as_posix(),
                "auditPath": audit_path.as_posix(),
                "extraction": system_extraction,
                "audit": system_audit,
                "missingAudit": not audit_path.exists(),
                "grades": grades,
                "gradeScore": score_grades(grades),
            }
        )

    return sorted(runs, key=lambda run: (run["copyNumber"], run["label"]))


def copy_number_from_stem(stem: str) -> int:
    match = re.search(r" \((\d+)\)$", stem)
    return int(match.group(1)) if match else 0


def build_manifest(
    *,
    reference_dir: Path,
    review_sources: list[tuple[str, Path]],
    image_dirs: list[Path],
    output_file: Path,
) -> dict[str, Any]:
    reference_extraction_dir = reference_dir / "extraction"
    reference_audit_dir = reference_dir / "audit_results"
    if not reference_extraction_dir.exists():
        raise FileNotFoundError(f"Reference extraction directory not found: {reference_extraction_dir}")

    receipts = []
    for extraction_path in sorted(reference_extraction_dir.glob("*.json")):
        stem = extraction_path.stem
        audit_path = reference_audit_dir / extraction_path.name
        reference_extraction = read_json(extraction_path)
        reference_audit = normalize_audit_fields(read_json(audit_path)) if audit_path.exists() else None
        receipts.append(
            {
                "stem": stem,
                "label": readable_label(stem),
                "image": find_receipt_image(stem, image_dirs, output_file),
                "reference": {
                    "extractionPath": extraction_path.as_posix(),
                    "auditPath": audit_path.as_posix(),
                    "extraction": reference_extraction,
                    "audit": reference_audit,
                    "missingAudit": not audit_path.exists(),
                },
                "runs": [
                    run
                    for source_label, source_dir in review_sources
                    for run in matching_review_runs(stem, source_label, source_dir, reference_audit, reference_extraction)
                ],
            }
        )

    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "referenceDir": reference_dir.as_posix(),
        "reviewSources": [
            {"label": source_label, "path": source_dir.as_posix()}
            for source_label, source_dir in review_sources
        ],
        "imageDirs": [path.as_posix() for path in image_dirs],
        "graders": [{"name": grader.name, "comment": grader.comment} for grader in RECEIPT_GRADERS],
        "receipts": receipts,
    }


def readable_label(stem: str) -> str:
    short_hash_removed = re.sub(r"_jpeg\.rf\.[a-f0-9]+$", "", stem)
    return short_hash_removed.replace("_", " ")


def html_document(manifest: dict[str, Any]) -> str:
    manifest_json = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Receipt Review Viewer</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #080b0f;
      --panel: #111720;
      --panel-2: #151d29;
      --panel-3: #0d1219;
      --text: #edf3f8;
      --muted: #91a0ae;
      --faint: #5c6a78;
      --border: #26313d;
      --accent: #53c6b8;
      --accent-2: #f4c95d;
      --danger: #ff6b78;
      --success: #68d391;
      --shadow: 0 22px 80px rgba(0, 0, 0, 0.38);
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      letter-spacing: 0;
    }}

    button,
    input,
    select {{
      font: inherit;
    }}

    .shell {{
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }}

    header {{
      border-bottom: 1px solid var(--border);
      background: rgba(8, 11, 15, 0.95);
      backdrop-filter: blur(14px);
      position: sticky;
      top: 0;
      z-index: 10;
    }}

    .topbar {{
      max-width: 1720px;
      margin: 0 auto;
      padding: 18px 22px;
      display: grid;
      grid-template-columns: minmax(260px, 1fr) minmax(520px, 1.6fr);
      gap: 22px;
      align-items: end;
    }}

    h1 {{
      margin: 0 0 7px;
      font-size: 22px;
      line-height: 1.15;
      font-weight: 720;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .controls {{
      display: grid;
      grid-template-columns: minmax(220px, 1.3fr) minmax(150px, 1fr) minmax(150px, 220px) minmax(170px, 240px) auto auto;
      gap: 10px;
      align-items: end;
    }}

    label {{
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.2;
      font-weight: 650;
    }}

    input,
    select {{
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #0c1118;
      color: var(--text);
      padding: 0 12px;
      outline: none;
    }}

    input:focus,
    select:focus {{
      border-color: rgba(83, 198, 184, 0.75);
      box-shadow: 0 0 0 3px rgba(83, 198, 184, 0.16);
    }}

    .toggle {{
      min-height: 42px;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 7px;
      background: #0c1118;
      color: var(--text);
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}

    .toggle input {{
      width: 15px;
      min-height: 15px;
      accent-color: var(--accent);
    }}

    main {{
      max-width: 1720px;
      width: 100%;
      margin: 0 auto;
      padding: 22px;
      display: grid;
      grid-template-columns: minmax(330px, 420px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }}

    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
    }}

    .image-panel {{
      position: sticky;
      top: 98px;
      overflow: hidden;
    }}

    .image-frame {{
      background: #05070a;
      border-bottom: 1px solid var(--border);
      min-height: 460px;
      max-height: calc(100vh - 220px);
      display: grid;
      place-items: center;
      overflow: auto;
    }}

    .image-frame img {{
      display: block;
      max-width: 100%;
      height: auto;
    }}

    .empty-image {{
      padding: 24px;
      color: var(--muted);
      text-align: center;
      line-height: 1.5;
    }}

    .receipt-info {{
      padding: 15px;
      display: grid;
      gap: 12px;
    }}

    .receipt-title {{
      margin: 0;
      font-size: 15px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }}

    .path-list {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    code {{
      font-family: var(--mono);
      color: #cbd7e3;
      overflow-wrap: anywhere;
    }}

    .field-code {{
      white-space: nowrap;
      overflow-wrap: normal;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 10px;
    }}

    .metric {{
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 12px;
      min-width: 0;
    }}

    .metric strong {{
      display: block;
      font-size: 20px;
      line-height: 1;
      margin-bottom: 7px;
    }}

    .metric span {{
      color: var(--muted);
      font-size: 12px;
    }}

    .tabs {{
      display: flex;
      gap: 6px;
      padding: 8px;
      border-bottom: 1px solid var(--border);
      background: var(--panel-3);
      border-radius: 8px 8px 0 0;
    }}

    .tab {{
      border: 1px solid transparent;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      padding: 9px 12px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 700;
    }}

    .tab[aria-selected="true"] {{
      color: var(--text);
      background: var(--panel-2);
      border-color: var(--border);
    }}

    .content {{
      padding: 16px;
      display: grid;
      gap: 18px;
    }}

    .section-title {{
      margin: 0 0 10px;
      color: var(--text);
      font-size: 14px;
      font-weight: 740;
    }}

    .table-wrap {{
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: auto;
      background: #0a0f15;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 820px;
    }}

    th,
    td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
      line-height: 1.45;
    }}

    th {{
      position: sticky;
      top: 0;
      background: #101721;
      z-index: 1;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0;
    }}

    tr:last-child td {{
      border-bottom: 0;
    }}

    tr.diff-row td {{
      background: rgba(255, 107, 120, 0.08);
    }}

    tr.pass-row td {{
      background: rgba(104, 211, 145, 0.035);
    }}

    .status {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 52px;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 11px;
      font-weight: 800;
    }}

    .status.pass {{
      color: #06120b;
      background: var(--success);
    }}

    .status.diff {{
      color: #17090b;
      background: var(--danger);
    }}

    .value {{
      font-family: var(--mono);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: #d8e3ed;
    }}

    .grade-cell {{
      display: grid;
      gap: 7px;
      min-width: 190px;
    }}

    .grade-meta {{
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}

    .grade-comment {{
      max-width: 420px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}

    .muted {{
      color: var(--muted);
    }}

    .warning {{
      border: 1px solid rgba(244, 201, 93, 0.4);
      color: #f6d986;
      background: rgba(244, 201, 93, 0.08);
      border-radius: 7px;
      padding: 12px;
      font-size: 13px;
      line-height: 1.5;
    }}

    .raw-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
    }}

    .raw-block {{
      display: grid;
      gap: 8px;
      min-width: 0;
    }}

    .raw-title {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      overflow-wrap: anywhere;
    }}

    pre {{
      margin: 0;
      min-height: 280px;
      max-height: 62vh;
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #06090d;
      padding: 14px;
      color: #d8e3ed;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}

    .empty-state {{
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }}

    .grader-summary-row {{
      cursor: pointer;
    }}

    .grader-expand-btn {{
      border: 0;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      padding: 0 6px 0 0;
      font-size: 11px;
      vertical-align: middle;
    }}

    .grader-details-row td {{
      background: var(--panel-3);
      border-bottom: 1px solid var(--border);
      padding-top: 0;
    }}

    .grader-details-row[hidden] {{
      display: none;
    }}

    .grader-run-breakdown {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 14px;
      padding: 0 12px 12px;
    }}

    .breakdown-heading {{
      font-size: 11px;
      font-weight: 750;
      color: var(--muted);
      text-transform: uppercase;
      margin-bottom: 8px;
    }}

    .run-outcome-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 6px;
    }}

    .run-jump {{
      display: flex;
      align-items: flex-start;
      gap: 8px;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #0a0f15;
      color: var(--text);
      padding: 8px 10px;
      cursor: pointer;
      text-align: left;
    }}

    .run-jump:hover {{
      border-color: rgba(83, 198, 184, 0.45);
    }}

    .run-jump-label {{
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }}

    @media (max-width: 980px) {{
      .topbar {{
        grid-template-columns: 1fr;
      }}

      .controls {{
        grid-template-columns: 1fr;
      }}

      main {{
        grid-template-columns: 1fr;
      }}

      .image-panel {{
        position: static;
      }}

      .image-frame {{
        min-height: 320px;
        max-height: none;
      }}

      .summary,
      .raw-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div class="topbar">
        <div>
          <h1>Receipt Review Viewer</h1>
          <div class="meta">
            <span id="generatedAt"></span>
            <span id="receiptCount"></span>
            <span id="runCount"></span>
          </div>
        </div>
        <div class="controls">
          <label>
            Receipt
            <select id="receiptSelect"></select>
          </label>
          <label>
            Search
            <input id="receiptSearch" list="receiptOptions" placeholder="Filter by merchant, file, or stem">
            <datalist id="receiptOptions"></datalist>
          </label>
          <label>
            Review source
            <select id="sourceSelect"></select>
          </label>
          <label>
            System run
            <select id="runSelect"></select>
          </label>
          <label class="toggle">
            <input id="compareAll" type="checkbox">
            Compare all runs
          </label>
          <label class="toggle">
            <input id="diffOnly" type="checkbox">
            Diffs only
          </label>
        </div>
      </div>
    </header>

    <main>
      <aside class="panel image-panel">
        <div class="image-frame" id="imageFrame"></div>
        <div class="receipt-info">
          <h2 class="receipt-title" id="receiptTitle"></h2>
          <div class="summary" id="summary"></div>
          <div class="path-list" id="pathList"></div>
        </div>
      </aside>

      <section class="panel">
        <div class="tabs" role="tablist" aria-label="Comparison views">
          <button class="tab" type="button" data-tab="extraction" aria-selected="true">Extraction</button>
          <button class="tab" type="button" data-tab="audit" aria-selected="false">Audit</button>
          <button class="tab" type="button" data-tab="graders" aria-selected="false">Graders</button>
          <button class="tab" type="button" data-tab="raw" aria-selected="false">Raw JSON</button>
        </div>
        <div class="content" id="content"></div>
      </section>
    </main>
  </div>

  <script>
    const MANIFEST = {manifest_json};

    const EXTRACTION_FIELDS = [
      ["merchant"],
      ["location", "city"],
      ["location", "state"],
      ["location", "zipcode"],
      ["time"],
      ["subtotal"],
      ["tax"],
      ["total"],
      ["handwritten_notes"],
    ];

    const ITEM_FIELDS = [
      "description",
      "product_code",
      "category",
      "item_price",
      "sale_price",
      "quantity",
      "total",
    ];

    const AUDIT_FIELDS = [
      ["not_travel_related"],
      ["amount_over_limit"],
      ["math_error"],
      ["handwritten_x"],
      ["line_item_extraction_warning"],
      ["needs_audit"],
    ];

    const SOURCES = MANIFEST.reviewSources || [];
    const ALL_SOURCES = -1;

    const state = {{
      receiptIndex: 0,
      sourceIndex: SOURCES.length > 1 ? ALL_SOURCES : 0,
      runIndex: 0,
      compareAll: false,
      tab: "extraction",
      diffOnly: false,
      expandedGraders: {{}},
    }};

    const receiptSelect = document.getElementById("receiptSelect");
    const receiptSearch = document.getElementById("receiptSearch");
    const receiptOptions = document.getElementById("receiptOptions");
    const sourceSelect = document.getElementById("sourceSelect");
    const runSelect = document.getElementById("runSelect");
    const compareAll = document.getElementById("compareAll");
    const diffOnly = document.getElementById("diffOnly");
    const content = document.getElementById("content");

    function activeSourceLabel() {{
      if (state.sourceIndex === ALL_SOURCES) return null;
      return SOURCES[state.sourceIndex] ? SOURCES[state.sourceIndex].label : null;
    }}

    function sourceRuns(receipt) {{
      const label = activeSourceLabel();
      if (label === null) return receipt.runs;
      return receipt.runs.filter(run => run.source === label);
    }}

    function runDisplayLabel(run) {{
      const base = runColumnLabel(run.label);
      return SOURCES.length > 1 ? `${{run.source}} · ${{base}}` : base;
    }}

    function isSelectableReceipt(receipt) {{
      return Boolean(receipt.image) && receipt.runs.length > 0;
    }}

    function selectableReceipts() {{
      return MANIFEST.receipts
        .map((receipt, index) => ({{ receipt, index }}))
        .filter(({{ receipt }}) => isSelectableReceipt(receipt));
    }}

    function receiptOptionLabel(receipt) {{
      const merchant = receipt.reference?.extraction?.merchant;
      const runCount = receipt.runs.length;
      const runSuffix = `${{runCount}} run${{runCount === 1 ? "" : "s"}}`;
      if (merchant) {{
        return `${{merchant}} — ${{receipt.label}} (${{runSuffix}})`;
      }}
      return `${{receipt.label}} (${{runSuffix}})`;
    }}

    function syncReceiptPicker() {{
      const receipt = currentReceipt();
      if (receiptSelect && [...receiptSelect.options].some(option => option.value === String(state.receiptIndex))) {{
        receiptSelect.value = String(state.receiptIndex);
      }}
      receiptSearch.value = receipt.label;
    }}

    function selectReceiptByIndex(manifestIndex) {{
      if (!MANIFEST.receipts[manifestIndex]) return;
      state.receiptIndex = manifestIndex;
      state.runIndex = 0;
      syncReceiptPicker();
      renderMain();
    }}

    function normalize(value) {{
      if (typeof value === "string") return value.trim().toLocaleLowerCase();
      if (Array.isArray(value)) return value.map(normalize).sort();
      if (value && typeof value === "object") {{
        return Object.keys(value).sort().reduce((next, key) => {{
          next[key] = normalize(value[key]);
          return next;
        }}, {{}});
      }}
      return value;
    }}

    function isEqual(left, right) {{
      return JSON.stringify(normalize(left)) === JSON.stringify(normalize(right));
    }}

    function valueAt(data, path) {{
      return path.reduce((value, key) => value && typeof value === "object" ? value[key] : undefined, data);
    }}

    function formatValue(value) {{
      if (value === undefined) return "missing";
      if (value === null) return "null";
      if (typeof value === "string") return value === "" ? '""' : value;
      return JSON.stringify(value, null, 2);
    }}

    function escapeHtml(value) {{
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function currentReceipt() {{
      return MANIFEST.receipts[state.receiptIndex];
    }}

    function currentRun() {{
      const receipt = currentReceipt();
      return sourceRuns(receipt)[state.runIndex] || null;
    }}

    function activeRuns() {{
      const receipt = currentReceipt();
      if (state.compareAll) return sourceRuns(receipt);
      const run = currentRun();
      return run ? [run] : [];
    }}

    function comparisonRows(kind) {{
      const receipt = currentReceipt();
      const runs = activeRuns();
      const rows = [];
      if (!runs.length) return rows;

      if (kind === "extraction") {{
        for (const path of EXTRACTION_FIELDS) {{
          rows.push(comparePath(
            path.join("."),
            valueAt(receipt.reference.extraction, path),
            runs.map(run => ({{ label: runDisplayLabel(run), value: valueAt(run.extraction, path) }}))
          ));
        }}

        const expectedItems = receipt.reference.extraction.items || [];
        const runItems = runs.map(run => (run.extraction.items || []));
        rows.push(comparePath(
          "item_count",
          expectedItems.length,
          runs.map((run, index) => ({{ label: runDisplayLabel(run), value: runItems[index].length }}))
        ));
        const maxItems = Math.max(expectedItems.length, ...runItems.map(items => items.length));
        for (let index = 0; index < maxItems; index += 1) {{
          for (const field of ITEM_FIELDS) {{
            rows.push(comparePath(
              `items[${{index}}].${{field}}`,
              expectedItems[index] ? expectedItems[index][field] : undefined,
              runs.map((run, runIndex) => ({{
                label: runDisplayLabel(run),
                value: runItems[runIndex][index] ? runItems[runIndex][index][field] : undefined
              }}))
            ));
          }}
        }}
      }}

      if (kind === "audit") {{
        if (!receipt.reference.audit || runs.some(run => !run.audit)) return rows;
        for (const path of AUDIT_FIELDS) {{
          rows.push(comparePath(
            path.join("."),
            valueAt(receipt.reference.audit, path),
            runs.map(run => ({{ label: runDisplayLabel(run), value: valueAt(run.audit, path) }}))
          ));
        }}
      }}

      return rows;
    }}

    function comparePath(label, expected, actuals) {{
      const runComparisons = actuals.map(actual => ({{
        ...actual,
        matches: isEqual(expected, actual.value),
      }}));
      return {{
        label,
        expected,
        actuals: runComparisons,
        matches: runComparisons.every(actual => actual.matches),
      }};
    }}

    function runColumnLabel(label) {{
      return label.replace(/\\.json$/, "").replace(/_jpeg\\.rf\\.[a-f0-9]+/, "");
    }}

    function renderComparisonTable(rows) {{
      const visibleRows = state.diffOnly ? rows.filter(row => !row.matches) : rows;
      if (!visibleRows.length) {{
        return `<div class="empty-state">${{state.diffOnly ? "No differences in this view." : "No comparison rows available."}}</div>`;
      }}

      const runHeaders = visibleRows[0].actuals.map(actual => actual.label);
      const minTableWidth = Math.max(820, 520 + runHeaders.length * 260);
      return `
        <div class="table-wrap">
          <table style="min-width: ${{minTableWidth}}px">
            <thead>
              <tr>
                <th>Status</th>
                <th>Field</th>
                <th>Train reference</th>
                ${{runHeaders.map(label => `<th>${{escapeHtml(runColumnLabel(label))}}</th>`).join("")}}
              </tr>
            </thead>
            <tbody>
              ${{visibleRows.map(row => `
                <tr class="${{row.matches ? "pass-row" : "diff-row"}}">
                  <td><span class="status ${{row.matches ? "pass" : "diff"}}">${{row.matches ? "PASS" : "DIFF"}}</span></td>
                  <td><code class="field-code">${{escapeHtml(row.label)}}</code></td>
                  <td><div class="value">${{escapeHtml(formatValue(row.expected))}}</div></td>
                  ${{row.actuals.map(actual => `<td><div class="value">${{escapeHtml(formatValue(actual.value))}}</div></td>`).join("")}}
                </tr>
              `).join("")}}
            </tbody>
          </table>
        </div>
      `;
    }}

    function renderExtraction() {{
      const rows = comparisonRows("extraction");
      content.innerHTML = `
        <section>
          <h3 class="section-title">Extraction Comparison</h3>
          ${{renderComparisonTable(rows)}}
        </section>
      `;
    }}

    function renderAudit() {{
      const receipt = currentReceipt();
      const runs = activeRuns();
      if (!runs.length) {{
        content.innerHTML = `<div class="empty-state">No saved system run found for this receipt.</div>`;
        return;
      }}
      if (receipt.reference.missingAudit || runs.some(run => run.missingAudit)) {{
        content.innerHTML = `<div class="warning">Audit comparison is incomplete because one audit JSON file is missing.</div>`;
        return;
      }}

      const rows = comparisonRows("audit");
      content.innerHTML = `
        <section>
          <h3 class="section-title">Audit Flag Comparison</h3>
          ${{renderComparisonTable(rows)}}
        </section>
        <section>
          <h3 class="section-title">Reasoning</h3>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Train reference</th>
                  ${{runs.map(run => `<th>${{escapeHtml(runDisplayLabel(run))}}</th>`).join("")}}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><div class="value">${{escapeHtml(formatValue(receipt.reference.audit.reasoning))}}</div></td>
                  ${{runs.map(run => `<td><div class="value">${{escapeHtml(formatValue(run.audit.reasoning))}}</div></td>`).join("")}}
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      `;
    }}

    function graderRows() {{
      const runs = activeRuns();
      return MANIFEST.graders.map(grader => {{
        const actuals = runs.map(run => {{
          const grade = (run.grades || []).find(candidate => candidate.name === grader.name);
          return {{
            label: run.label,
            grade,
            matches: Boolean(grade && grade.passed),
          }};
        }});
        return {{
          name: grader.name,
          comment: grader.comment,
          actuals,
          matches: actuals.every(actual => actual.matches),
        }};
      }});
    }}

    function gradeDetails(grade) {{
      if (!grade || !grade.details || !Object.keys(grade.details).length) return "";
      return `<div class="grade-meta">details: ${{escapeHtml(JSON.stringify(grade.details, null, 2))}}</div>`;
    }}

    function renderGradeCell(grade) {{
      if (!grade) {{
        return `
          <div class="grade-cell">
            <span class="status diff">MISS</span>
            <div class="grade-meta">No grade was produced for this run.</div>
          </div>
        `;
      }}
      return `
        <div class="grade-cell">
          <span class="status ${{grade.passed ? "pass" : "diff"}}">${{grade.passed ? "PASS" : "FAIL"}}</span>
          <div class="grade-meta">expected: ${{escapeHtml(formatValue(grade.expected))}}
actual:   ${{escapeHtml(formatValue(grade.actual))}}</div>
          ${{gradeDetails(grade)}}
        </div>
      `;
    }}

    function allRunGraderTotals() {{
      const totals = Object.fromEntries(MANIFEST.graders.map(grader => [
        grader.name,
        {{
          name: grader.name,
          comment: grader.comment,
          passed: 0,
          failed: 0,
          total: 0,
          passPercentage: 0,
          passedRuns: [],
          failedRuns: [],
        }}
      ]));

      const activeLabel = activeSourceLabel();
      MANIFEST.receipts.forEach((receipt, receiptIndex) => {{
        receipt.runs.forEach((run, runIndex) => {{
          if (activeLabel !== null && run.source !== activeLabel) return;
          for (const grade of run.grades || []) {{
            if (!totals[grade.name]) continue;
            totals[grade.name].total += 1;
            const runEntry = {{
              receiptIndex,
              runIndex,
              receiptLabel: receipt.label,
              runLabel: runDisplayLabel(run),
            }};
            if (grade.passed) {{
              totals[grade.name].passed += 1;
              totals[grade.name].passedRuns.push(runEntry);
            }} else {{
              totals[grade.name].failed += 1;
              totals[grade.name].failedRuns.push(runEntry);
            }}
          }}
        }});
      }});

      for (const total of Object.values(totals)) {{
        total.passPercentage = total.total ? Math.round((total.passed / total.total) * 1000) / 10 : 0;
      }}
      return Object.values(totals);
    }}

    function renderRunOutcomeList(runs, outcome) {{
      if (!runs.length) {{
        return `<span class="muted">None</span>`;
      }}
      return `
        <ul class="run-outcome-list">
          ${{runs.map(run => `
            <li>
              <button
                type="button"
                class="run-jump"
                data-receipt-index="${{run.receiptIndex}}"
                data-run-index="${{run.runIndex}}"
              >
                <span class="status ${{outcome === "pass" ? "pass" : "diff"}}">${{outcome === "pass" ? "PASS" : "FAIL"}}</span>
                <span class="run-jump-label">${{escapeHtml(run.receiptLabel)}} · ${{escapeHtml(run.runLabel)}}</span>
              </button>
            </li>
          `).join("")}}
        </ul>
      `;
    }}

    function toggleGraderBreakdown(graderName) {{
      state.expandedGraders[graderName] = !state.expandedGraders[graderName];
      const summaryRow = content.querySelector(`[data-grader-summary="${{CSS.escape(graderName)}}"]`);
      const detailsRow = content.querySelector(`[data-grader-details="${{CSS.escape(graderName)}}"]`);
      if (!summaryRow || !detailsRow) return;

      const expanded = Boolean(state.expandedGraders[graderName]);
      detailsRow.hidden = !expanded;
      const button = summaryRow.querySelector(".grader-expand-btn");
      if (button) {{
        button.textContent = expanded ? "▼" : "▶";
        button.setAttribute("aria-expanded", String(expanded));
      }}
    }}

    function jumpToRun(receiptIndex, runIndex) {{
      const receipt = MANIFEST.receipts[receiptIndex];
      const run = receipt ? receipt.runs[runIndex] : null;
      if (!run) return;

      state.receiptIndex = receiptIndex;
      if (activeSourceLabel() !== null && run.source !== activeSourceLabel()) {{
        const sourceIndex = SOURCES.findIndex(source => source.label === run.source);
        state.sourceIndex = sourceIndex === -1 ? ALL_SOURCES : sourceIndex;
      }}
      state.runIndex = sourceRuns(receipt).indexOf(run);
      state.compareAll = false;
      state.tab = "graders";
      syncReceiptPicker();
      document.querySelectorAll(".tab").forEach(tab => {{
        tab.setAttribute("aria-selected", String(tab.dataset.tab === "graders"));
      }});
      renderMain();
    }}

    function renderGraderPercentages() {{
      const totals = allRunGraderTotals();
      const sourceLabel = activeSourceLabel();
      const scopeSuffix = SOURCES.length > 1 ? ` — ${{sourceLabel === null ? "all sources" : sourceLabel}}` : "";
      return `
        <section>
          <h3 class="section-title">All Runs Pass Rate${{escapeHtml(scopeSuffix)}}</h3>
          <p class="muted" style="margin: 0 0 10px; font-size: 12px; line-height: 1.45;">
            Click a grader row to see which runs passed or failed. Click a run to jump to it.
          </p>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Grader</th>
                  <th>Pass rate</th>
                  <th>Passed</th>
                  <th>Failed</th>
                  <th>What it checks</th>
                </tr>
              </thead>
              <tbody>
                ${{totals.map(total => {{
                  const expanded = Boolean(state.expandedGraders[total.name]);
                  return `
                    <tr
                      class="grader-summary-row ${{total.failed ? "diff-row" : "pass-row"}}"
                      data-grader-summary="${{escapeHtml(total.name)}}"
                      data-grader-toggle="${{escapeHtml(total.name)}}"
                    >
                      <td>
                        <button
                          type="button"
                          class="grader-expand-btn"
                          aria-expanded="${{expanded}}"
                          aria-label="Show runs for ${{escapeHtml(total.name)}}"
                          data-grader-toggle="${{escapeHtml(total.name)}}"
                        >${{expanded ? "▼" : "▶"}}</button>
                        <code class="field-code">${{escapeHtml(total.name)}}</code>
                      </td>
                      <td><strong>${{total.passPercentage}}%</strong></td>
                      <td><code>${{total.passed}}/${{total.total}}</code></td>
                      <td><code>${{total.failed}}</code></td>
                      <td><div class="grade-comment">${{escapeHtml(total.comment)}}</div></td>
                    </tr>
                    <tr
                      class="grader-details-row"
                      data-grader-details="${{escapeHtml(total.name)}}"
                      ${{expanded ? "" : "hidden"}}
                    >
                      <td colspan="5">
                        <div class="grader-run-breakdown">
                          <div>
                            <div class="breakdown-heading">Passed (${{total.passedRuns.length}})</div>
                            ${{renderRunOutcomeList(total.passedRuns, "pass")}}
                          </div>
                          <div>
                            <div class="breakdown-heading">Failed (${{total.failedRuns.length}})</div>
                            ${{renderRunOutcomeList(total.failedRuns, "fail")}}
                          </div>
                        </div>
                      </td>
                    </tr>
                  `;
                }}).join("")}}
              </tbody>
            </table>
          </div>
        </section>
      `;
    }}

    function renderGraders() {{
      const runs = activeRuns();
      if (!runs.length) {{
        content.innerHTML = `<div class="empty-state">No saved system run found for this receipt.</div>`;
        return;
      }}

      const rows = graderRows();
      const visibleRows = state.diffOnly ? rows.filter(row => !row.matches) : rows;
      if (!visibleRows.length) {{
        content.innerHTML = `
          ${{renderGraderPercentages()}}
          <div class="empty-state">No grader failures in this view.</div>
        `;
        return;
      }}

      const minTableWidth = Math.max(860, 560 + runs.length * 260);
      content.innerHTML = `
        ${{renderGraderPercentages()}}
        <section>
          <h3 class="section-title">Grader Results</h3>
          <div class="table-wrap">
            <table style="min-width: ${{minTableWidth}}px">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Grader</th>
                  <th>What it checks</th>
                  ${{runs.map(run => `<th>${{escapeHtml(runDisplayLabel(run))}}</th>`).join("")}}
                </tr>
              </thead>
              <tbody>
                ${{visibleRows.map(row => `
                  <tr class="${{row.matches ? "pass-row" : "diff-row"}}">
                    <td><span class="status ${{row.matches ? "pass" : "diff"}}">${{row.matches ? "PASS" : "FAIL"}}</span></td>
                    <td><code class="field-code">${{escapeHtml(row.name)}}</code></td>
                    <td><div class="grade-comment">${{escapeHtml(row.comment)}}</div></td>
                    ${{row.actuals.map(actual => `<td>${{renderGradeCell(actual.grade)}}</td>`).join("")}}
                  </tr>
                `).join("")}}
              </tbody>
            </table>
          </div>
        </section>
      `;
    }}

    function renderRaw() {{
      const receipt = currentReceipt();
      const runs = activeRuns();
      if (!runs.length) {{
        content.innerHTML = `<div class="empty-state">No saved system run found for this receipt.</div>`;
        return;
      }}

      content.innerHTML = `
        <section>
          <h3 class="section-title">Extraction JSON</h3>
          <div class="raw-grid">
            <div class="raw-block">
              <div class="raw-title">Train reference</div>
              <pre>${{escapeHtml(JSON.stringify(receipt.reference.extraction, null, 2))}}</pre>
            </div>
            ${{runs.map(run => `
              <div class="raw-block">
                <div class="raw-title">${{escapeHtml(runDisplayLabel(run))}}</div>
                <pre>${{escapeHtml(JSON.stringify(run.extraction, null, 2))}}</pre>
              </div>
            `).join("")}}
          </div>
        </section>
        <section>
          <h3 class="section-title">Audit JSON</h3>
          <div class="raw-grid">
            <div class="raw-block">
              <div class="raw-title">Train reference</div>
              <pre>${{escapeHtml(JSON.stringify(receipt.reference.audit, null, 2))}}</pre>
            </div>
            ${{runs.map(run => `
              <div class="raw-block">
                <div class="raw-title">${{escapeHtml(runDisplayLabel(run))}}</div>
                <pre>${{escapeHtml(JSON.stringify(run.audit, null, 2))}}</pre>
              </div>
            `).join("")}}
          </div>
        </section>
      `;
    }}

    function renderImage() {{
      const receipt = currentReceipt();
      const imageFrame = document.getElementById("imageFrame");
      if (receipt.image) {{
        imageFrame.innerHTML = `<img src="${{escapeHtml(receipt.image)}}" alt="Receipt image for ${{escapeHtml(receipt.stem)}}">`;
      }} else {{
        imageFrame.innerHTML = `<div class="empty-image">No matching receipt image found in the configured image directories.</div>`;
      }}
    }}

    function renderSummary() {{
      const extractionRows = comparisonRows("extraction");
      const auditRows = comparisonRows("audit");
      const gradeRows = graderRows();
      const allRows = extractionRows.concat(auditRows);
      const diffs = allRows.filter(row => !row.matches).length;
      const runs = sourceRuns(currentReceipt()).length;
      const gradeFailures = gradeRows.reduce(
        (total, row) => total + row.actuals.filter(actual => !actual.matches).length,
        0
      );
      const activeGradePassed = activeRuns().reduce((total, run) => total + (run.gradeScore ? run.gradeScore.passed : 0), 0);
      const activeGradeTotal = activeRuns().reduce((total, run) => total + (run.gradeScore ? run.gradeScore.total : 0), 0);

      document.getElementById("summary").innerHTML = `
        <div class="metric"><strong>${{diffs}}</strong><span>Total diffs</span></div>
        <div class="metric"><strong>${{extractionRows.filter(row => !row.matches).length}}</strong><span>Extraction diffs</span></div>
        <div class="metric"><strong>${{auditRows.filter(row => !row.matches).length}}</strong><span>Audit diffs</span></div>
        <div class="metric"><strong>${{gradeFailures}}</strong><span>Grade failures</span></div>
      `;

      document.getElementById("pathList").innerHTML = `
        <div><span class="muted">Runs:</span> <code>${{runs}}</code></div>
        <div><span class="muted">Grade score:</span> <code>${{activeGradePassed}}/${{activeGradeTotal}}</code></div>
        <div><span class="muted">Reference extraction:</span> <code>${{escapeHtml(currentReceipt().reference.extractionPath)}}</code></div>
        <div><span class="muted">System run:</span> <code>${{escapeHtml(state.compareAll ? `all runs (${{runs}})` : currentRun() ? runDisplayLabel(currentRun()) : "missing")}}</code></div>
      `;
    }}

    function renderControls() {{
      const receipt = currentReceipt();

      sourceSelect.innerHTML = "";
      if (SOURCES.length > 1) {{
        const allOption = document.createElement("option");
        allOption.value = String(ALL_SOURCES);
        allOption.textContent = "All sources";
        sourceSelect.append(allOption);
      }}
      SOURCES.forEach((source, index) => {{
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = source.label;
        sourceSelect.append(option);
      }});
      sourceSelect.value = String(state.sourceIndex);
      sourceSelect.disabled = SOURCES.length < 2;

      const runs = sourceRuns(receipt);
      if (runs.length < 2) state.compareAll = false;
      if (state.runIndex >= runs.length) state.runIndex = 0;
      runSelect.innerHTML = "";
      if (!runs.length) {{
        const option = document.createElement("option");
        option.value = "-1";
        option.textContent = "No saved run";
        runSelect.append(option);
        runSelect.disabled = true;
        compareAll.disabled = true;
      }} else {{
        compareAll.disabled = runs.length < 2;
        compareAll.checked = state.compareAll && runs.length > 1;
        runSelect.disabled = state.compareAll;
        runs.forEach((run, index) => {{
          const option = document.createElement("option");
          option.value = String(index);
          option.textContent = runDisplayLabel(run);
          runSelect.append(option);
        }});
        runSelect.value = String(state.runIndex);
      }}
    }}

    function renderMain() {{
      const receipt = currentReceipt();
      document.getElementById("receiptTitle").textContent = receipt.label;
      renderControls();
      renderImage();
      renderSummary();

      if (!currentRun()) {{
        const sourceLabel = activeSourceLabel();
        content.innerHTML = `<div class="empty-state">No saved system run found in ${{escapeHtml(sourceLabel === null ? "any review source" : `review source "${{sourceLabel}}"`)}} for this train reference.</div>`;
        return;
      }}

      if (state.tab === "extraction") renderExtraction();
      if (state.tab === "audit") renderAudit();
      if (state.tab === "graders") renderGraders();
      if (state.tab === "raw") renderRaw();
    }}

    function selectReceiptByQuery(query) {{
      const normalizedQuery = query.trim().toLocaleLowerCase();
      if (!normalizedQuery) return;

      const candidates = selectableReceipts();
      const exactMatch = candidates.find(({{ receipt }}) =>
        receipt.stem.toLocaleLowerCase() === normalizedQuery ||
        receipt.label.toLocaleLowerCase() === normalizedQuery
      );
      const fuzzyMatch = candidates.find(({{ receipt }}) => {{
        const merchant = (receipt.reference?.extraction?.merchant || "").toLocaleLowerCase();
        return receipt.stem.toLocaleLowerCase().includes(normalizedQuery) ||
          receipt.label.toLocaleLowerCase().includes(normalizedQuery) ||
          merchant.includes(normalizedQuery);
      }});
      const match = exactMatch || fuzzyMatch;
      if (match) {{
        selectReceiptByIndex(match.index);
      }}
    }}

    function initialize() {{
      document.getElementById("generatedAt").textContent = `Generated ${{MANIFEST.generatedAt}}`;
      const reviewableReceipts = selectableReceipts();
      document.getElementById("receiptCount").textContent =
        `${{reviewableReceipts.length}} reviewable receipts`;
      document.getElementById("runCount").textContent =
        `${{MANIFEST.receipts.reduce((total, receipt) => total + receipt.runs.length, 0)}} system runs`;

      if (!MANIFEST.receipts.length) {{
        content.innerHTML = `<div class="empty-state">No train references found.</div>`;
        return;
      }}

      if (!reviewableReceipts.length) {{
        content.innerHTML = `<div class="empty-state">No receipts with saved system runs and images were found.</div>`;
        return;
      }}

      receiptSelect.innerHTML = reviewableReceipts.map(({{ receipt, index }}) => `
        <option value="${{index}}">${{escapeHtml(receiptOptionLabel(receipt))}}</option>
      `).join("");

      receiptOptions.innerHTML = reviewableReceipts.map(({{ receipt }}) =>
        `<option value="${{escapeHtml(receipt.label)}}"></option>`
      ).join("");

      state.receiptIndex = reviewableReceipts[0].index;
      syncReceiptPicker();

      receiptSelect.addEventListener("change", event => {{
        selectReceiptByIndex(Number(event.target.value));
      }});

      receiptSearch.addEventListener("change", event => selectReceiptByQuery(event.target.value));
      receiptSearch.addEventListener("keydown", event => {{
        if (event.key === "Enter") selectReceiptByQuery(event.target.value);
      }});

      sourceSelect.addEventListener("change", event => {{
        state.sourceIndex = Number(event.target.value);
        state.runIndex = 0;
        renderMain();
      }});

      runSelect.addEventListener("change", event => {{
        state.runIndex = Number(event.target.value);
        renderMain();
      }});

      compareAll.addEventListener("change", event => {{
        state.compareAll = event.target.checked;
        renderMain();
      }});

      diffOnly.addEventListener("change", event => {{
        state.diffOnly = event.target.checked;
        renderMain();
      }});

      content.addEventListener("click", event => {{
        const toggle = event.target.closest("[data-grader-toggle]");
        if (toggle) {{
          event.preventDefault();
          toggleGraderBreakdown(toggle.dataset.graderToggle);
          return;
        }}

        const jump = event.target.closest(".run-jump");
        if (jump) {{
          event.preventDefault();
          jumpToRun(Number(jump.dataset.receiptIndex), Number(jump.dataset.runIndex));
        }}
      }});

      document.querySelectorAll(".tab").forEach(tab => {{
        tab.addEventListener("click", () => {{
          state.tab = tab.dataset.tab;
          document.querySelectorAll(".tab").forEach(nextTab => {{
            nextTab.setAttribute("aria-selected", String(nextTab === tab));
          }});
          renderMain();
        }});
      }});

      renderMain();
    }}

    initialize();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a static receipt review comparison viewer.")
    parser.add_argument("--reference-dir", default="outputs/train_reference")
    parser.add_argument(
        "--reviews-dir",
        action="append",
        help=(
            "Review source as 'label=path' or plain 'path'. Repeat to compare multiple saved runs. "
            "Defaults to outputs/reviews plus every snapshot under outputs/runs/."
        ),
    )
    parser.add_argument("--image-dir", action="append", default=["data/train", "data/test"])
    parser.add_argument("--output", default="outputs/review_viewer/index.html")
    args = parser.parse_args()

    review_sources = (
        [parse_review_source(value) for value in args.reviews_dir]
        if args.reviews_dir
        else default_review_sources()
    )
    output_file = Path(args.output)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(
        reference_dir=Path(args.reference_dir),
        review_sources=review_sources,
        image_dirs=[Path(path) for path in args.image_dir],
        output_file=output_file,
    )
    output_file.write_text(html_document(manifest), encoding="utf-8")
    print(f"Wrote {output_file}")
    print(f"Receipts: {len(manifest['receipts'])}")
    print(f"System runs: {sum(len(receipt['runs']) for receipt in manifest['receipts'])}")


if __name__ == "__main__":
    main()
