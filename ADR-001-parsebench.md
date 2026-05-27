# Using ParseBench to Evaluate Custom Document-Parsing Pipelines

- **Date:** 2026-05-27

---

## 1. Context

We need a defensible, repeatable way to evaluate document-parsing tools (PDF → structured output suitable for downstream AI agents) across our own custom pipelines and the third-party services on the market. Traditional OCR benchmarks (PubTabNet, FinTabNet, DocLayNet, ChartQA) each cover only a narrow slice — table recognition, layout, or chart QA — and most grade output on "looks similar" rather than "structurally correct enough for an agent to act on."

**ParseBench** (LlamaIndex) is the benchmark that fills this gap. It evaluates ~2,078 human-verified enterprise PDF pages with **169,011 strict, deterministic rules** across five capability dimensions. Crucially, it ships a CLI plus a pluggable provider/adapter system that lets us register **our own custom pipelines** alongside the 130+ reference pipelines (LlamaParse, GPT-5, Gemini 3, Claude Haiku 4.5, Docling, Textract, DocAI, Reducto, Qwen3-VL, Dots-OCR, etc.) and score them on the exact same rules and dataset used to produce the public leaderboard.

This document describes ParseBench and documents how to wire a custom pipeline into it.

---

## 2. Why ParseBench (vs. alternatives)

| Benchmark | Scope | Limitation |
|---|---|---|
| PubTabNet | Table recognition only | Narrow, table-specific |
| FinTabNet | Financial tables only | Sub-task only |
| DocLayNet | Layout detection only | No content / semantic correctness |
| ChartQA | Chart Q&A | Doesn't extract chart data to structured form |
| **ParseBench** | **5 dimensions, 167k+ rules, 2k pages** | **End-to-end, agent-grade structural correctness** |

The five dimensions ParseBench scores:

1. **Visual Grounding** — bounding boxes back to original pixels (inspires `data-bbox`).
2. **Tables** — multi-page tables with merged cells / hierarchical headers (inspires `colspan`/`rowspan`).
3. **Charts** — pixel chart → exact data table with 1% tolerance.
4. **Semantic Formatting** — meaning-carrying formatting (strikethrough = invalidated price, superscript = footnote, not the digit 1).
5. **Content Faithfulness** — every glyph in correct reading order, no hallucinated or dropped digits.

Evaluation is **deterministic and rule-based by default** — no LLM-as-judge in the scoring path. The only LLM call inside evaluation is an opt-in chart-label normalization (gated by `LLAMACLOUD_BENCH_LLM_NORMALIZATION=judge`, off by default). Our trust in the metric is therefore not coupled to a black-box judge.

---

## 3. What ParseBench's reference runs tell us

ParseBench's own paper benchmarks 14 systems across three families:

- **Native VLMs** (single-shot prompting): GPT-5 Mini, Haiku 4.5, Gemini 3 Flash, Qwen3-VL, Dots-OCR 1.5 (best content faithfulness at 90.0).
- **Specialized parsers / OCR**: Docling OSS, AWS Textract, Google DocAI, Azure DI, Reducto, Extend, Landing AI.
- **Agentic / hybrid**: LlamaParse Cost Effective and LlamaParse Agentic (**leaderboard top at 84.9%**).

**Key insight:** the agentic multi-pass approach beats single-shot VLM prompting because it handles "layout fatigue" — the longer-context error compounding a single LLM hits — by routing each element class (tables, charts, formulas, headers) to a specialist agent and running a verification pass.

---

## 4. Local results on our 12-page test subset

Numbers from `--test` mode (3 PDFs/category × 4 categories = 12 pages). Small subset; treat as rough signal, not leaderboard.

| Pipeline | Chart | Layout AP@50 | Layout RPR | Tables GriTS | Faithfulness | Sem. Fmt | Latency | $/page |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **LlamaParse Agentic Plus** | **1.00** | 0.68 | 0.73 | **0.97** | 0.84 | **0.64** | 34.2 s | $0.0563 |
| LlamaParse Cost Effective | 0.33 | 0.62 | 0.72 | 0.97 | **0.85** | 0.00 | 26.1 s | $0.0038 |
| Gemini 3 Flash Thinking Minimal | 0.64 | **0.88** | **0.87** | 0.97 | 0.84 | 0.14 | **7.6 s** | $0.0028 |
| **Custom · Gemini 3 Flash (thinking medium)** | **0.74** | 0.87 | 0.84 | 0.97 | **0.85** | 0.14 | 13.4 s | ~$0.001 |
| Custom · Gemini 2.5 Flash | 0.00 | 0.79 | 0.72 | 0.97 | 0.82 | 0.14 | 20.2 s | ~$0.001 |
| Custom · Docling + EasyOCR | 0.00 | 0.49 | 0.75 | 0.74 | 0.83 | 0.27 | 38.6 s | $0.00 |
| Custom · Docling + RapidOCR | 0.00 | 0.49 | 0.75 | 0.74 | 0.83 | 0.27 | 22.2 s | $0.00 |
| PyMuPDF (text) | 0.00 | n/a | n/a | 0.00 | 0.83 | 0.00 | 0.5 s | $0.00 |
| PyPDF baseline | 0.00 | n/a | n/a | 0.00 | 0.83 | 0.00 | 0.6 s | $0.00 |

### Tiered conclusions

- **Tier 1 (production-grade):** LlamaParse Agentic Plus wins charts (1.00) and semantic formatting (0.64 — 2.4× the next best). Dedicated chart-to-table agent + verification pass. ~$56/1000 pages.
- **Tier 2 (value):** Gemini 3 Flash Thinking Minimal — fastest LLM (7.6 s), 20× cheaper than Agentic Plus, edges layout dimensions.
- **Tier 3 (our custom):** `custom_multimodal` with `gemini-3-flash-preview` + `reasoning_effort=medium` **matches or beats the built-in Google pipeline on every quality dimension** (Chart 0.74 vs 0.64, Faithfulness 0.85 vs 0.84, tied on Tables/Sem Fmt). Loses ~3 pts on Layout RPR and is ~2× slower, attributable to SDK vs raw-HTTP infrastructure.
- **Tier 4 (local, $0):** Docling + RapidOCR (force_full_page_ocr=False) — 0.75 layout RPR, 0.74 tables, 0.83 faithfulness, best Sem Fmt of any local (0.27). Docling does **not** transcribe chart datapoints (scores 0).
- **Tier 5 (sanity check):** PyPDF / PyMuPDF — surprisingly competitive on faithfulness (0.83) at ~50× lower latency. Zero structural ability. Use only as reading-order baseline.

---

## 5. ParseBench Codebase Overview

ParseBench is a benchmark for evaluating document-parsing tools (PDF → structured markdown/HTML/JSON). Five components wired together by a Fire CLI at [src/parse_bench/cli.py:262](src/parse_bench/cli.py#L262):

| Layer | Path | Role |
|---|---|---|
| Data | [src/parse_bench/data/](src/parse_bench/data/) | Downloads ~2,000 verified pages from HuggingFace |
| Inference | [src/parse_bench/inference/](src/parse_bench/inference/) | Pipelines (`parse`, `extract`, `layout`) call providers (LlamaParse, OpenAI, Anthropic, Google, etc.) and emit `InferenceResult` |
| Evaluation | [src/parse_bench/evaluation/](src/parse_bench/evaluation/) | Applies rules/metrics to actual vs. expected → `EvaluationResult` |
| Analysis | [src/parse_bench/analysis/](src/parse_bench/analysis/) | Cross-category dashboards, leaderboard |
| Reports | [src/parse_bench/evaluation/reports/](src/parse_bench/evaluation/reports/) | HTML, CSV, Markdown, rule-level CSV |

### 5.1 How rules are set up

Rules are **not** pre-registered in a global registry. They live in the ground-truth JSONL files shipped with the dataset (e.g. `text_content.jsonl`, `table.jsonl`, `chart.jsonl`, `text_formatting.jsonl`, `layout.jsonl`) and are instantiated dynamically.

- **Schemas / discriminated union**: [src/parse_bench/test_cases/parse_rule_schemas.py:34](src/parse_bench/test_cases/parse_rule_schemas.py#L34) defines `ParseRuleBase` and per-type schemas (`ParsePresenceRule`, `ParseTableRule`, `ParseFormFieldRule`, …).
- **Factory**: `create_test_rule()` at [parse_rule_schemas.py:424](src/parse_bench/test_cases/parse_rule_schemas.py#L424) picks the right class from the `type` field.
- **Executors** (one file per family) live under [evaluation/metrics/parse/](src/parse_bench/evaluation/metrics/parse/):
  - `rules_bag.py` — sentence/word presence, absence, occurrence count
  - `rules_text.py` — text order / baseline
  - `rules_table.py` — colspan/rowspan/adjacency over parsed HTML grids
  - `rules_form.py` — form fields
  - `rules_chart.py` — chart data points, label arrays, value arrays
  - `rules_formatting.py` — bold, italic, strikeout, superscript, mark color, …
- **Runner**: `RuleBasedMetric.compute()` at [rule_based_metric.py:41](src/parse_bench/evaluation/metrics/parse/rule_based_metric.py#L41) normalizes the model output once, executes each rule's `run(md, normalized)`, aggregates pass/fail into a `rule_pass_rate`.

Each rule returns a simple `(passed, explanation[, score])` tuple — a deterministic contract.

### 5.2 Categories and how they are scored

| Dimension | Source | Scoring | LLM? |
|---|---|---|---|
| **Tables** | `table.jsonl` | GriTS + TableRecordMatch (2D grid IoU + LCS, continuous F-score) | No |
| **Charts** | `chart.jsonl` | Exact / fuzzy string + numeric matching of label & data arrays | **Optional** post-hoc LLM normalization |
| **Content faithfulness** | `text_content.jsonl` | `MISSING_SENTENCE`, `UNEXPECTED_SENTENCE`, occurrence-count rules — substring/token matching | No |
| **Semantic formatting** | `text_formatting.jsonl` | `IS_BOLD`, `IS_STRIKEOUT`, `IS_SUPERSCRIPT`, etc. — markdown/HTML pattern matching | No |
| **Visual grounding / forms** | `layout.jsonl` | Form-field fuzzy label+value matching, IoU on bounding boxes | No |

**Everything is deterministic by default.** The only exception is opt-in chart-label normalization at [evaluation/metrics/parse/llm_normalization/postprocess.py:275](src/parse_bench/evaluation/metrics/parse/llm_normalization/postprocess.py#L275), gated by `LLAMACLOUD_BENCH_LLM_NORMALIZATION=judge` (off by default; runs Claude Haiku 4.5 only on already-failed chart rules to decide if `"Entity"` vs `"Year"` should count as semantically equivalent).

### 5.3 How to plug in a custom pipeline

Use the repo's built-in slash command — it is the canonical, up-to-date recipe and walks Claude Code through every step end-to-end:

```
/integrate-pipeline <provider_name> <docs-url-or-context>
```

#### Gap: layout adapter (may be required)

The skill currently **does not mention the layout adapter step.** If your provider emits per-element bounding boxes and you want a non-zero Layout score, you must also register a layout adapter in [src/parse_bench/evaluation/layout_adapters/adapters.py](src/parse_bench/evaluation/layout_adapters/adapters.py). It must live there — **not** inside the provider file — because evaluation workers do not import provider modules, so a decorator-registered adapter colocated with the provider never reaches the registry.

### 5.4 Code Issues

- `GOOGLE_GEMINI_API_KEY` is the env var (README says `GOOGLE_API_KEY` — wrong).
- Free-tier Gemini = 5 RPM → run with `--max_concurrent 1`.
- `docling_parse` pipeline is a **client** for a self-hosted Docling-Serve server, not a local runner. For purely local Docling use the `custom_docling` provider added in this repo.
- Re-runs need `--force` or stale `.result.json` files produce `skipped` rows.
- Windows: stdout/`write_text`/`read_text` must be UTF-8 (fixed in [cli.py](src/parse_bench/cli.py), [runner.py](src/parse_bench/inference/runner.py), and the three report writers).
- `pytest` is not a declared dev dep — `uv pip install pytest` manually.

---

## 6. Pipeline architectures we tested

### 6.1 `custom_multimodal` — Gemini 2.5 Flash

Single-shot VLM pipeline on `gemini-2.5-flash`. Splits PDF into single-page PDFs → sends each page to Gemini with a layout-extraction prompt → parses `<div data-bbox=… data-label=…>` blocks → assembles `ParseOutput` with per-element bboxes.

- **Result:** Chart 0.00, Layout AP@50 0.79, Tables 0.97, Faithfulness 0.82, Sem Fmt 0.14, ~20 s/page.
- **Use as:** cheap baseline; loses most of the structural-output quality vs. the 3-Flash variant.

### 6.2 `custom_multimodal` — Gemini 3 Flash Thinking Medium

Same provider plumbing as 6.1, but on `gemini-3-flash-preview` with `reasoning_effort="medium"` (2048 thinking tokens). The thinking budget is what gives this variant its lift over 2.5.

- **Result:** Chart **0.74**, Layout AP@50 0.87, Tables 0.97, Faithfulness **0.85**, Sem Fmt 0.14, ~13 s/page, ~$0.001/page.
- **Vs. built-in `google_gemini_3_flash_thinking_minimal`:** matches or beats it on every quality dimension (Chart 0.74 vs 0.64, Faithfulness 0.85 vs 0.84, tied on Tables/Sem Fmt). Loses ~3 pts on Layout RPR and is ~2× slower — pure infrastructure gap (qualitative `medium` thinking vs the built-in's fixed budget; `requests.post` per page vs google-genai SDK's HTTP/2 keep-alive).
- **Weakness:** generative predictor has no "ruler" — borderless tables and complex charts still produce structural guesses; Sem Fmt at 0.14 reflects that it is "structurally smart, stylistically blind."

### 6.3 `custom_docling` (fully local)

Six-stage pipeline, fully offline:

- **A. Page prep** — PyPDFium renders each page to a hi-res bitmap.
- **B. Layout detection** — `docling-project/docling-layout-heron` (ONNX) returns clusters `(bbox, label)` for {Title, Section-header, Text, List-item, Table, Picture, Page-header, Page-footer, Caption, Footnote, Formula, Code}. `create_orphan_clusters=True` preserves stragglers.
- **C. Per-cluster text** — automatic per-cluster choice: reuse embedded PDF text stream where selectable, OR run OCR (EasyOCR or RapidOCR) on the bitmap region.
- **D. Table structure** — TableFormer (ACCURATE mode), `do_cell_matching=True` reuses Stage-C text rather than re-OCRing.
- **E. Document assembly** — reading-order solver (column-aware) builds typed `DoclingDocument` tree.
- **F. Export** — markdown with tables exported as HTML via `item.export_to_html(doc)` for GriTS compatibility.

**EasyOCR vs RapidOCR observation:** EasyOCR and RapidOCR (with `force_full_page_ocr=False`) score **identically on every quality dimension**. RapidOCR is ~2× faster with smaller models — better default. Docling reuses the embedded PDF text on most pages, so the OCR backend only matters for genuinely scanned pages. Forcing full-page OCR via `force_full_page_ocr=True` introduces OCR errors on already-selectable text and drops Faithfulness from 0.83 → 0.69.

Charts score 0 because Docling has no chart-to-datapoints agent.

### 6.4 Google Gemini 3 Flash Thinking Minimal — Parse With Layout File (llm-based reference)

Built-in ParseBench pipeline that runs `gemini-3-flash-preview` with **minimal** thinking and a deterministic layout-file guide. Four mode variants exist (`file`, `image`, `parse_with_layout`, `parse_with_layout_file`); the layout-file mode is the strongest:

- **Layout File Augmentation** — Before the page image ever reaches Gemini, a deterministic layout-analysis tool (Docling/Heron or similar) processes the PDF and emits a structural "Layout File" (JSON/XML) with bounding boxes for every paragraph, table, header.
- **Guided Reasoning** — The layout file is injected into Gemini's context window alongside the page image: "Here is the image, and here is a map of where all the important things are."
- **Flash Thinking Inference** — Gemini runs an internal chain-of-thought before emitting the final extraction, using the layout file to verify its own logic (e.g. confirming text inside a given bbox should be parsed as a table row rather than free-floating text).

**Why it's a useful reference:**

- **Exceptional Tables (79.3%, GriTS-Con 97.4%)** — the layout file is the hero; the model no longer has to "guess" grid lines.
- **Strong Layout Attribution (78.1%, AP@50 88.0%)** — text-to-bbox mapping is near-perfect.
- **Text Formatting weakness (13.6%)** — "structurally smart, stylistically blind"; even with a layout file, Gemini struggles to tell cosmetic styles (subtle strikeout, bold) from structural headings.
- **Headers/footers 0.0% pass rate** — pipeline faithfully extracts everything including page chrome, because it isn't told to filter `Page-header`/`Page-footer` clusters.

### 6.5 LlamaParse Agentic Plus (agentic-based reference)

Six-stage hosted pipeline that defines the upper bound:

- **A. Ingestion + hi-DPI render.**
- **B. Layout detection** (fine-tuned DiT/LayoutLMv3 or VLM-as-detector).
- **C. Element-specific specialist agents** — the differentiator vs. plain `agentic`:
  - Tables → table-structure model + verification re-prompt.
  - **Charts → dedicated chart-to-table agent** (perfect 1.00 score — only pipeline that doesn't degrade charts to `[Figure: ...]` captions).
  - Pictures → captioner.
  - Formulas → math-OCR → LaTeX.
  - Inline formatting (bold/strikethrough/super-sub) preserved → drives the 0.64 Sem-Fmt score.
- **D. Quality/consistency pass** — re-validates each element against the page image; fixes multi-column reading order; reconciles cross-page tables. This pass is why `agentic_plus` is 4.5× the cost of `agentic` (45 vs 10 credits/page).
- **E. Output assembly.**

---

## 7. Trade-offs and open follow-ups

**Strengths**

- Single, deterministic, rule-based source of truth for parsing quality across 5 capability dimensions.
- Custom pipelines compete on the same rules and dataset as the public leaderboard.
- Cost/quality tradeoffs are quantified per pipeline (see §4 table).
- Local/offline option (Docling + RapidOCR) validated for air-gapped use cases.

**Caveats**

- Some "local-sounding" reference pipelines (`docling_parse`, `tesseract_*`, `unstructured_*`, `qwen3*_vllm_*`) require external binaries, self-hosted vLLM servers, or paid API keys and do not run out-of-the-box.

**Open follow-ups**

- Run all custom pipelines on the **full** 2,078-page dataset (not `--test`) for production-grade numbers.
- Add improvements in custom pipelines for rules which are failing to improve those pass rate.
- Evaluate a chart-specialist agent on top of `custom_multimodal` to close the chart gap with LlamaParse Agentic Plus.

---

## 8. References

- [Github](https://github.com/run-llama/ParseBench)
- [Available Pipelines](https://github.com/run-llama/ParseBench/blob/main/docs/pipelines.md)
- [Paper Published](https://arxiv.org/abs/2604.08538)
- [Huggingface Dataset](https://huggingface.co/datasets/llamaindex/ParseBench)
- [Leaderboards](https://github.com/run-llama/ParseBench/blob/main/leaderboard.csv)
- [Website](https://www.parsebench.ai/)
