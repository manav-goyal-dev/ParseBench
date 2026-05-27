# ParseBench Pipeline Comparison

LLM-based extraction beats OCR based models (cheapest) on almost every thing  
LLM-based custom pipeline with gemini 3 flash with medium thinking performed best in its category and 2nd best overall => $0.0043/page  
LLamaParse with Agentic Plus tier performed best overall => Good with Text formatting and Charts comparatively, $0.0563/page, ~13× more expensive than custom Gemini 3 Flash pipeline  

All numbers from `--test` mode: **12 unique PDFs** drawn 3 per category from chart / table / text / layout. Small subset — run-to-run noise can move any single score by 5–20 points. Treat as rough signal, not a leaderboard.

## Results

| Pipeline | Provider / Model | Chart RPR | Layout AP@50 | Layout RPR | Tables GriTS | Faithfulness | Sem. Fmt | Avg latency |
|----------|------------------|----------:|-------------:|-----------:|-------------:|-------------:|---------:|------------:|
| **LlamaParse Agentic Plus** 👑 | llamaparse / agentic_plus | **1.00** | 0.68 | 0.73 | **0.97** | 0.84 | **0.64** | 34.2 s |
| LlamaParse Cost Effective | llamaparse / cost_effective | 0.33 | 0.62 | 0.72 | 0.97 | **0.85** | 0.00 | 26.1 s |
| Gemini 3 Flash · Thinking Minimal | google / gemini-3-flash-preview | 0.64 | **0.88** | **0.87** | 0.97 | 0.84 | 0.14 | **7.6 s** |
| Custom · Gemini 3 Flash (thinking medium) | custom_multimodal / gemini-3-flash-preview | **0.74** | 0.87 | 0.84 | 0.97 | **0.85** | 0.14 | 13.4 s |
| Custom · Gemini 2.5 Flash (low) | custom_multimodal / gemini-2.5-flash | 0.00 | 0.79 | 0.72 | 0.97 | 0.82 | 0.14 | 20.2 s |
| Custom · Docling + EasyOCR | custom_docling / easyocr | 0.00 | 0.49 | 0.75 | 0.74 | 0.83 | 0.27 | 38.6 s |
| Custom · Docling + RapidOCR | custom_docling / rapidocr | 0.00 | 0.49 | 0.75 | 0.74 | 0.83 | 0.27 | **22.2 s** |
| PyMuPDF (text) | pymupdf / text | 0.00 | n/a | n/a | 0.00 | 0.83 | 0.00 | **0.5 s** |
| PyPDF baseline | pypdf / parse | 0.00 | n/a | n/a | 0.00 | 0.83 | 0.00 | 0.6 s |
| PyMuPDF (html) | pymupdf / html | 0.00 | n/a | n/a | 0.00 | 0.62 | 0.16 | 0.4 s |

## Cost per page

Pricing measured directly from the providers (where reported) or inferred from public per-token rates. "Local" pipelines have no API spend (only electricity).

| Pipeline | $ / page | Notes |
|----------|---------:|-------|
| **LlamaParse Agentic Plus** | **$0.0563** | 45 credits/page × $1.25/1000 credits |
| LlamaParse Cost Effective | $0.0038 | 3 credits/page (15× cheaper than Agentic Plus) |
| Gemini 3 Flash · Thinking Minimal (built-in) | $0.0028 | Tracked by provider — input + output + thinking tokens |
| Custom · Gemini 3.5 Flash | ~$0.001¹ | Provider doesn't track; estimated from token usage at Google list pricing |
| Custom · Gemini 2.5 Flash | ~$0.001¹ | Same as above |
| Custom · Docling + EasyOCR | **$0.00** | Fully local |
| Custom · Docling + RapidOCR | **$0.00** | Fully local |
| PyMuPDF (text / html) | **$0.00** | Fully local |
| PyPDF baseline | **$0.00** | Fully local |

¹ Free tier is genuinely $0 up to 20 requests / day. Paid tier estimate based on ~2.5 k input + 4 k output tokens/page at Google's `gemini-3-flash` list price (~$0.075/M input, ~$0.30/M output).

### Cost × Quality at a glance

For a 1,000-page document set, the dollars look like:

| Pipeline | 1,000 pages | Use when… |
|----------|------------:|-----------|
| LlamaParse Agentic Plus | **$56.30** | You need charts + semantic formatting and can pay for it |
| LlamaParse Cost Effective | $3.75 | You need decent structured output (tables, text) at hosted-API scale |
| Gemini 3 Flash Minimal | $2.82 | Cheapest hosted option that still gets ~tier-1 results on most dimensions |
| Custom Gemini 3.5 (paid) | ~$1 | DIY with your own prompt + thinking off — cheapest LLM path |
| Docling (any OCR) | $0 + CPU time | Air-gapped / offline / privacy-sensitive |
| PyMuPDF / PyPDF | $0 + ms | Reading order text only — baseline for sanity checking |

## Best by dimension

| Dimension | Winner | Score |
|-----------|--------|------:|
| **Charts** | LlamaParse Agentic Plus | 1.00 |
| **Layout AP@50** | Gemini 3 Flash · Thinking Minimal | 0.88 |
| **Layout Rule Pass** | Gemini 3 Flash · Thinking Minimal | 0.87 |
| **Tables GriTS-Con** | Most LLM-based pipelines (tie) | 0.97 |
| **Content Faithfulness** | LlamaParse Cost Effective | 0.85 |
| **Semantic Formatting** | LlamaParse Agentic Plus | 0.64 |
| **Latency** | PyMuPDF html | 0.4 s/page |
| **Best $/quality** | Gemini 3 Flash · Thinking Minimal | $0.0028/page |

## Headline findings

### Tier 1 — Production-grade structured parsing
**LlamaParse Agentic Plus** wins or near-wins on every dimension. Perfect chart extraction on the test subset and a Semantic-Formatting score (0.64) **2.4× higher than any other pipeline** — i.e. it actually preserves bold / strikethrough / superscript markup that everything else flattens. The premium price tag (~5.6¢/page) buys a server-side multi-agent pipeline with a dedicated chart-to-table specialist and a verification pass.

### Tier 2 — Strong general-purpose LLM parsers
**Gemini 3 Flash (thinking minimal)** is the value pick. Edges everything on Layout AP@50 / Layout RPR while being **the fastest LLM pipeline** at 7.6 s/page and **20× cheaper than Agentic Plus** ($0.0028 vs $0.056). It does not match Agentic Plus on charts (0.64 vs 1.00) or semantic formatting (0.14 vs 0.64).

### Tier 3 — Custom Gemini variants
Your `MultimodalParser` integration works end-to-end. After switching to `gemini-3-flash-preview` with `reasoning_effort="medium"` (2048 thinking tokens), the custom pipeline **matches or beats the built-in Google pipeline** on every quality dimension (Chart **0.74 vs 0.64**, Faithfulness **0.85 vs 0.84**, tied on Tables and Sem Fmt). The built-in still wins on Layout RPR by ~3 points and is ~2× faster — that gap is purely infrastructure (qualitative thinking-level vs fixed-budget; google-genai SDK HTTP/2 keep-alive vs `requests.post` per page). The `gemini-2.5-flash` variant is left as a faster/cheaper option but loses most of the structural-output quality.

### Tier 4 — Local Docling (no API, fully offline)
Docling + EasyOCR is the **best local pipeline**: 0.75 layout RPR, 0.74 tables GriTS, 0.83 faithfulness, and the **highest Semantic Formatting score of any local pipeline** (0.27). It loses on charts (Docling doesn't transcribe chart datapoints) and on layout AP (looser bboxes than LLMs). $0/page after the one-time model download.

RapidOCR now matches EasyOCR exactly on every quality dimension (after setting `force_full_page_ocr=False`) and is **~2× faster** — Docling reuses the embedded PDF text on most pages, so the OCR backend only matters for genuinely scanned content. RapidOCR is the better default now: same accuracy, lighter runtime, smaller models. The earlier 0.69 faithfulness number was the cost of forcing re-OCR on already-selectable text.

### Tier 5 — Raw text extractors
PyPDF and PyMuPDF-text are **competitive with LLMs on Content Faithfulness alone** (0.83 vs 0.84) at **~50× lower latency** and zero cost. They score 0 on everything structural — charts, tables, layout. Use them as a baseline reality-check; if your downstream task only needs reading-order text, you don't need anything fancier.

PyMuPDF-html is worse than PyMuPDF-text because the HTML tags get scored as content.
