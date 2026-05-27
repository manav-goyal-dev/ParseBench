# ParseBench — Notes

## What it is
ParseBench is a benchmark CLI (`uv run parse-bench ...`) for evaluating document-parsing tools that convert PDFs into structured output. Evaluation is fully deterministic / rule-based (no LLM-as-judge); API keys are only used to call the parser under test.

## Running the tests

```powershell
uv pip install pytest        # not in pyproject deps; had to install manually
uv run python -m pytest tests/
```

Result: **182 passed, 0 failed, 1 warning** in ~16s.

### What works
- All 182 tests pass cleanly.
- Tests cover: form-field rule extraction, runner aggregation, Infinity-Parser2 provider, data-dir routing, extract integration, field-grounding adapter.

### What doesn't / minor issues
- `pytest` is **not declared as a dev dependency** in [pyproject.toml](pyproject.toml). Fresh `uv sync` does not install it; you must `uv pip install pytest` manually.
- One collection warning: [src/parse_bench/evaluation/metrics/parse/test_types.py:6](src/parse_bench/evaluation/metrics/parse/test_types.py#L6) — `TestType` is a `StrEnum` (domain type) but pytest tries to collect it because of the `Test*` name. Silence with `__test__ = False` or rename. Not a failure.
- Working tree has uncommitted local changes in [src/parse_bench/data/cli.py](src/parse_bench/data/cli.py) and `uv.lock` (pre-existing, unrelated to test run).

## Test inventory (182 total, by file)

| File | Tests | Area |
|------|------:|------|
| [tests/parse_bench/evaluation/metrics/parse/test_form_field_rule.py](tests/parse_bench/evaluation/metrics/parse/test_form_field_rule.py) | ~135 | Form-field rule matching (labels, checkboxes, tables, page scoping, fuzzy/exact tie-breaks, strikethrough, dot-strip, etc.) |
| [tests/test_extract_integration.py](tests/test_extract_integration.py) | 9 | Extract pipeline + evaluator integration |
| [tests/test_data_dir_routing.py](tests/test_data_dir_routing.py) | 10 | `download` / `status` / `run` data-dir routing |
| [tests/parse_bench/inference/providers/parse/test_infinity_parser2.py](tests/parse_bench/inference/providers/parse/test_infinity_parser2.py) | 11 | Infinity-Parser2 cell classifiers, header detection, nonstandard tables |
| [tests/parse_bench/evaluation/test_runner_aggregation.py](tests/parse_bench/evaluation/test_runner_aggregation.py) | 1 | Macro/micro aggregation in evaluation runner |
| [tests/parse_bench/evaluation/metrics/field_grounding/test_extract_adapter.py](tests/parse_bench/evaluation/metrics/field_grounding/test_extract_adapter.py) | ~16 | Extract → field-grounding adapter |

## Available pipelines (parsing tools)
- **131 pipelines** registered (`uv run parse-bench pipelines`).
- 21 "paper baseline" pipelines highlighted in the README (LlamaParse, OpenAI GPT-5 family, Anthropic Haiku/Opus, Gemini 3, Azure DI, AWS Textract, Google DocAI, Reducto, Extend, LandingAI, Qwen3-VL, Dots-OCR, Docling, etc.).

## Dataset
~2,078 unique pages / 1,211 docs from HuggingFace `llamaindex/ParseBench`, split into 5 capability dimensions:

| Dimension | File | Metric | Pages | Docs | Rules |
|-----------|------|--------|------:|-----:|------:|
| Tables | `table.jsonl` | GTRM (GriTS + TableRecordMatch) | 503 | 284 | — |
| Charts | `chart.jsonl` | ChartDataPointMatch | 568 | 99 | 4,864 |
| Content Faithfulness | `text_content.jsonl` | Faithfulness Score | 506 | 506 | 141,322 |
| Semantic Formatting | `text_formatting.jsonl` | Formatting Score | 476 | 476 | 5,997 |
| Visual Grounding | `layout.jsonl` | Element Pass Rate | 500 | 321 | 16,325 |
| **Total (unique)** | | | **2,078** | **1,211** | **169,011** |

## CLI surface
`run`, `download`, `status`, `pipelines`, `compare`, `leaderboard`, `serve`, plus advanced subcommands: `inference`, `evaluation`, `analysis`, `pipeline`, `data`.

## Bottom line
Repo is healthy: full test suite green. Only papercuts are the missing `pytest` dev dep and one collection-name warning.

---

## Pipeline runs (--test mode, 3 files/category, 12 unique files)

| Pipeline | Result | Avg latency | Notes |
|----------|--------|-------------|-------|
| `llamaparse_agentic` | ✅ **12/12** (100%) | 23.6 s/page | Top tier — `agentic` config |
| `llamaparse_cost_effective` | ✅ **12/12** (100%) | 23.3 s/page | Cheaper LlamaParse tier |
| `google_gemini_3_flash_thinking_minimal_*` | ✅ **12/12** (100%) | 8.6 s/page | Only after `--max_concurrent 1` (free tier = 5 RPM) |
| `anthropic_haiku_*` | ❌ 0/12 | — | `.env` key 401-invalid |
| `openai_gpt5_mini_*` | ❌ 0/12 | — | Key authenticates but account has `insufficient_quota` (no billing) |
| `docling_parse` | ⏭ skipped | — | Pipeline requires `DOCLING_PARSE_ENDPOINT_URL` — it's a client to a self-hosted Docling-Serve server, not a local runner. No purely-local docling pipeline is registered. |

### Side-by-side scores (12 successful pages each)

| Dimension | Metric | LlamaParse Agentic | LlamaParse Cost-Eff | Gemini 3 Flash |
|-----------|--------|-------------------:|--------------------:|---------------:|
| Charts | Rule pass rate | **0.63** | 0.33 | 0.50 |
| Layout | AP@50 / AF1 | 0.60 / 0.93 | 0.68 / 0.98 | **0.88 / 0.96** |
| Tables | GriTS-Con / TRM | 0.97 / 0.79 | (n/a) | **0.97 / 0.79** |
| Content Faithfulness | Faithfulness | 0.84 | (n/a) | 0.84 |
| Semantic Formatting | Norm. text score | 0.14 | **0.39** | 0.14 |

(Numbers are on the tiny `--test` subset; not comparable to full leaderboard.)

### Custom pipeline integrated
A user-supplied `MultimodalParser` was wired in as the `custom_multimodal` provider with two pipeline variants (`custom_multimodal_gemini_2_5_flash`, `custom_multimodal_gemini_3_5_flash`). Three pieces of glue were needed:

1. **Provider class** — [src/parse_bench/inference/providers/parse/custom_multimodal.py](src/parse_bench/inference/providers/parse/custom_multimodal.py): splits PDF per page, calls Gemini, swaps `[y,x,y,x]` → `[x,y,x,y]` bboxes.
2. **Pipeline registration** — two `PipelineSpec` entries in [src/parse_bench/inference/pipelines/parse.py](src/parse_bench/inference/pipelines/parse.py).
3. **Layout adapter** — must live in [src/parse_bench/evaluation/layout_adapters/adapters.py](src/parse_bench/evaluation/layout_adapters/adapters.py), NOT in the provider file: eval workers don't import parse-provider modules, so a decorator-registered adapter inside the provider file never reaches the registry.

Watch out for: **`__init__.py` swallows `ImportError`** for every provider — silent failures look like "No provider registered for 'X'". Test with `python -c "import parse_bench.inference.providers.parse.<name>"` if a custom provider mysteriously vanishes.

### Run-to-run variance is real
A second `llamaparse_cost_effective --test --force` produced very different numbers from the first (~15 min later, same code, same dataset):

| | Run 1 | Run 2 |
|-|------:|------:|
| Avg latency | 23.3 s | 90.4 s |
| Chart pass | 0.33 | 0.11 |
| Layout AP@50 | 0.68 | 0.36 |
| Tables GriTS | n/a | 0.56 |
| Semantic Fmt | 0.39 | 0.07 |

So API-side non-determinism (sampling, backend routing, traffic conditions) is large on this tiny 12-page subset. Real leaderboard numbers come from the full 2,078-page run where noise averages out.

## Bugs found & fixed during runs

Three real bugs surfaced from running pipelines on Windows; all fixed:

1. **stdout/stderr crashed on unicode glyphs (✓, ⠋, ►)** — Windows console defaults to cp1252. Fixed by reconfiguring `sys.stdout`/`sys.stderr` to UTF-8 at CLI entry: [src/parse_bench/cli.py:9-17](src/parse_bench/cli.py#L9-L17).
2. **`write_text(...)` calls with no `encoding=` argument** wrote with locale codec, so parsed output containing curly quotes / degree signs / en-dashes crashed mid-run. Fixed in:
   - [src/parse_bench/inference/runner.py](src/parse_bench/inference/runner.py) (5 write sites)
   - [src/parse_bench/evaluation/cli.py:174](src/parse_bench/evaluation/cli.py#L174)
   - [src/parse_bench/evaluation/reports/markdown.py:97](src/parse_bench/evaluation/reports/markdown.py#L97)
   - [src/parse_bench/evaluation/reports/html.py:337](src/parse_bench/evaluation/reports/html.py#L337)
   - [src/parse_bench/evaluation/reports/rule_csv.py:21](src/parse_bench/evaluation/reports/rule_csv.py#L21)
3. **Matching `read_text(...)` calls** that re-read those files would then fail. Fixed at [inference/runner.py:275,286](src/parse_bench/inference/runner.py#L275) and [evaluation/cli.py:281,293](src/parse_bench/evaluation/cli.py#L281).

After fix: leaderboard regenerates clean and Gemini pipeline now exits 0 (only remaining failures are HTTP 429 quota, not code).

## Truly-local / no-API pipelines — what's possible on this box

Every "open / local-sounding" pipeline in the registry actually needs one of: a system binary, a self-hosted inference server, or a hosted API key. Out of the box on this Windows machine, **none of them run without extra setup**.

| Pipeline family | What it really needs | Status here |
|-----------------|----------------------|-------------|
| `tesseract_*` (eng, fast, high_quality) | `tesseract` OCR binary on PATH | Not installed. `choco install tesseract` requires admin (auto-mode blocked it). User can install manually from UB Mannheim, then `tesseract_fast --test` runs fully offline. |
| `docling_parse`, `docling_serve` | Running Docling-Serve HTTP server, `DOCLING_PARSE_ENDPOINT_URL` | Not running |
| `qwen3*_vllm_*`, `chandra2_vllm`, `dots_ocr_*_vllm` | Self-hosted vLLM server hosting that model | Not running |
| `surya_layout`, `chandra_layout` | Local PyTorch + model weights | Provider classes exist but not wired without an endpoint |
| `unstructured_*` | `UNSTRUCTURED_API_KEY` — hosted SaaS, not local despite the name | No key |

**Bottom line:** all currently-runnable pipelines on this box use hosted APIs (LlamaParse, OpenAI, Anthropic, Google).

## Gotchas discovered

1. **Google env var** — pipelines read `GOOGLE_GEMINI_API_KEY`, not `GOOGLE_API_KEY` as the README implies. Set the exact name in `.env`.
2. **Free-tier rate limits** — default `--max_concurrent 20` overwhelms free-tier Gemini (5 RPM). Use `--max_concurrent 1` for any free Google account.
3. **`docling_parse` is not local** — despite the name, it's a Docling-Serve client. To benchmark Docling end-to-end you must stand up Docling-Serve separately and set `DOCLING_PARSE_ENDPOINT_URL`.
4. **`pytest` not in dev deps** — install with `uv pip install pytest` before running the suite.
5. **`--force` is required to refresh a pipeline directory** — otherwise prior `.result.json` files cause `skipped` rows in the new summary.

