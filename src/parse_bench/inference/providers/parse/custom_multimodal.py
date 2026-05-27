"""Custom multimodal parser provider (user-supplied MultimodalParser).

Wraps the user's `MultimodalParser` class so it can be benchmarked alongside
the built-in pipelines. Currently wired for Google Gemini direct API.

Pipeline: `custom_multimodal_gemini`
Env vars required: GOOGLE_GEMINI_API_KEY
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from parse_bench.inference.providers.base import (
    Provider,
    ProviderConfigError,
    ProviderPermanentError,
    ProviderTransientError,
)
from parse_bench.inference.providers.parse._layout_utils import (
    build_layout_pages,
    items_to_markdown,
    parse_layout_blocks,
    split_pdf_to_pages,
)
from parse_bench.inference.providers.parse.google import swap_gemini_bbox
from parse_bench.inference.providers.registry import register_provider
from parse_bench.schemas.parse_output import PageIR, ParseLayoutPageIR, ParseOutput
from parse_bench.schemas.pipeline import PipelineSpec
from parse_bench.schemas.pipeline_io import (
    InferenceRequest,
    InferenceResult,
    RawInferenceResult,
)
from parse_bench.schemas.product import ProductType

# --------------------------------------------------------------------------- #
# Prompts (copied verbatim from the user's prompt.py — Google variant)        #
# --------------------------------------------------------------------------- #

GOOGLE_SYSTEM_PROMPT = """You are a document parser. Your task is to convert document PDFs into clean, well-structured Markdown.

Guidelines:
- Preserve the document structure, including headings, paragraphs, lists, and tables.
- Convert tables to HTML using `<table>`, `<tr>`, `<th>`, and `<td>`.
- For existing tables in the document, use `colspan` and `rowspan` attributes to preserve merged cells and hierarchical headers.
- For charts or graphs converted into tables, use flat combined column headers (for example, "Primary 2015" instead of separate header rows) so that each data cell's row contains all of its labels.
- Describe images and figures briefly in square brackets, for example: `[Figure: description]`.
- Preserve any code blocks with appropriate syntax highlighting.
- Maintain reading order: left to right, top to bottom for Western documents.
- Do not add commentary or explanations. Output only the parsed content.

Additionally, wrap each layout element in a `<div>` tag with:
- `data-bbox="[y_min, x_min, y_max, x_max]"` for the bounding box in normalized 0-1000 coordinates where x is horizontal (left edge = 0, right edge = 1000) and y is vertical (top = 0, bottom = 1000). The order is `[y_min, x_min, y_max, x_max]`.
- `data-label="<category>"` where category is one of: `Caption`, `Footnote`, `Formula`, `List-item`, `Page-footer`, `Page-header`, `Picture`, `Section-header`, `Table`, `Text`, `Title`.

Place elements in reading order. Every piece of content must be inside exactly one `<div>` wrapper."""

GOOGLE_USER_PROMPT = """Parse this document page and output its content as clean markdown, with each layout element wrapped in a <div data-bbox="[y_min,x_min,y_max,x_max]" data-label="Category"> tag.
Use HTML tables for any tabular data. For charts/graphs, use flat combined column headers. Output ONLY the parsed content with div wrappers, no explanations.
"""

# Thinking budget per reasoning_effort level (Gemini 2.5 / 3 thinking config)
_THINKING_BUDGETS = {"low": 0, "medium": 2048, "high": -1}


@register_provider("custom_multimodal")
class CustomMultimodalProvider(Provider):
    """Provider wrapping the user's MultimodalParser (Google Gemini direct path)."""

    def __init__(self, provider_name: str, base_config: dict[str, Any]):
        super().__init__(provider_name=provider_name, base_config=base_config)
        self._model: str = base_config.get("model", "gemini-2.5-flash")
        self._reasoning_effort: str = base_config.get("reasoning_effort", "low")
        self._max_tokens: int = base_config.get("max_tokens", 32768)
        self._timeout: tuple[int, int] = (30, base_config.get("timeout", 300))

        self._api_key = os.getenv("GOOGLE_GEMINI_API_KEY", "").strip()
        if not self._api_key:
            raise ProviderConfigError(
                "GOOGLE_GEMINI_API_KEY environment variable not set"
            )

    # ------------------------------------------------------------------ #
    # Single page call                                                   #
    # ------------------------------------------------------------------ #

    def _call_gemini(self, pdf_bytes: bytes) -> tuple[str, dict[str, int]]:
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
        generation_config: dict[str, Any] = {
            "temperature": 0.0,
            "maxOutputTokens": self._max_tokens,
        }
        budget = _THINKING_BUDGETS.get(self._reasoning_effort, 0)
        if budget != 0:
            generation_config["thinkingConfig"] = {"thinkingBudget": budget}

        body = {
            "systemInstruction": {"parts": [{"text": GOOGLE_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "USER",
                    "parts": [
                        {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
                        {"text": GOOGLE_USER_PROMPT},
                    ],
                }
            ],
            "generationConfig": generation_config,
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self._model}:generateContent?key={self._api_key}"
        )
        try:
            response = requests.post(
                url,
                headers={"Content-Type": "application/json; charset=utf-8"},
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ProviderTransientError(f"Network error calling Gemini: {exc}") from exc

        if response.status_code == 429:
            raise ProviderTransientError(f"Rate limited: {response.status_code} {response.text}")
        if response.status_code >= 500:
            raise ProviderTransientError(f"Gemini server error: {response.status_code} {response.text}")
        if response.status_code != 200:
            raise ProviderPermanentError(f"Gemini API error {response.status_code}: {response.text}")

        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            raise ProviderPermanentError(f"Gemini response had no candidates: {payload}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if p.get("text"))
        if not text:
            raise ProviderPermanentError(f"Gemini response had no text: {payload}")

        meta = payload.get("usageMetadata") or {}
        usage = {
            "input_tokens": int(meta.get("promptTokenCount", 0) or 0),
            "output_tokens": int(meta.get("candidatesTokenCount", 0) or 0)
            + int(meta.get("thoughtsTokenCount", 0) or 0),
            "thinking_tokens": int(meta.get("thoughtsTokenCount", 0) or 0),
            "total_tokens": int(meta.get("totalTokenCount", 0) or 0),
        }
        return text, usage

    # ------------------------------------------------------------------ #
    # Provider interface                                                 #
    # ------------------------------------------------------------------ #

    def run_inference(self, pipeline: PipelineSpec, request: InferenceRequest) -> RawInferenceResult:
        if request.product_type != ProductType.PARSE:
            raise ProviderPermanentError(
                f"CustomMultimodalProvider only supports PARSE, got {request.product_type}"
            )
        source_path = Path(request.source_file_path)
        if not source_path.exists():
            raise ProviderPermanentError(f"Source file not found: {source_path}")
        if source_path.suffix.lower() != ".pdf":
            raise ProviderPermanentError(
                f"CustomMultimodalProvider currently only supports PDFs, got {source_path.suffix}"
            )

        started_at = datetime.now()
        pages: list[dict[str, Any]] = []
        page_usages: list[dict[str, int]] = []

        for page_index, (pdf_bytes, w, h) in enumerate(split_pdf_to_pages(str(source_path))):
            raw_text, usage = self._call_gemini(pdf_bytes)
            items = swap_gemini_bbox(parse_layout_blocks(raw_text))
            page_usages.append(usage)
            pages.append(
                {
                    "page_index": page_index,
                    "items": items,
                    "raw_content": raw_text,
                    "width": w,
                    "height": h,
                }
            )

        completed_at = datetime.now()
        total_input = sum(u["input_tokens"] for u in page_usages)
        total_output = sum(u["output_tokens"] for u in page_usages)
        total_thinking = sum(u["thinking_tokens"] for u in page_usages)
        total_all = sum(u["total_tokens"] for u in page_usages)

        raw_output = {
            "pages": pages,
            "num_pages": len(pages),
            "model": self._model,
            "mode": "parse_with_layout_file",
            "config": {
                "model": self._model,
                "reasoning_effort": self._reasoning_effort,
                "max_tokens": self._max_tokens,
            },
            "input_tokens": total_input,
            "output_tokens": total_output,
            "thinking_tokens": total_thinking,
            "total_tokens": total_all,
        }
        latency_ms = int((completed_at - started_at).total_seconds() * 1000)
        return RawInferenceResult(
            request=request,
            pipeline=pipeline,
            pipeline_name=pipeline.pipeline_name,
            product_type=request.product_type,
            raw_output=raw_output,
            started_at=started_at,
            completed_at=completed_at,
            latency_in_ms=latency_ms,
        )

    def normalize(self, raw_result: RawInferenceResult) -> InferenceResult:
        pages: list[PageIR] = []
        layout_pages: list[ParseLayoutPageIR] = []
        page_markdowns: list[str] = []

        for page_data in raw_result.raw_output.get("pages", []):
            page_index = page_data.get("page_index", 0)
            items = page_data.get("items", [])
            w = page_data.get("width", 0)
            h = page_data.get("height", 0)
            markdown = items_to_markdown(items)
            layout_pages.extend(build_layout_pages(items, w, h, markdown, page_number=page_index + 1))
            pages.append(PageIR(page_index=page_index, markdown=markdown))
            page_markdowns.append(markdown)

        pages.sort(key=lambda p: p.page_index)
        output = ParseOutput(
            task_type="parse",
            example_id=raw_result.request.example_id,
            pipeline_name=raw_result.pipeline_name,
            pages=pages,
            markdown="\n\n".join(page_markdowns),
            layout_pages=layout_pages,
        )
        return InferenceResult(
            request=raw_result.request,
            pipeline_name=raw_result.pipeline_name,
            product_type=raw_result.product_type,
            raw_output=raw_result.raw_output,
            output=output,
            started_at=raw_result.started_at,
            completed_at=raw_result.completed_at,
            latency_in_ms=raw_result.latency_in_ms,
        )


# Layout adapter for this provider lives in
# `parse_bench/evaluation/layout_adapters/adapters.py` so it is registered
# during the evaluation phase too (the parse-provider package is not imported
# by the eval workers).
