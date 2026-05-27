"""Custom Docling-based parse provider (local, no API key).

Extracts:
- Per-page markdown with **HTML tables** (Docling default is markdown tables,
  which ParseBench's GriTS metric can't score).
- Per-element bounding boxes (so the Layout dimension can be evaluated).

Pipeline: `custom_docling_local`
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from parse_bench.inference.providers.base import (
    Provider,
    ProviderPermanentError,
)
from parse_bench.inference.providers.parse._layout_utils import build_layout_pages
from parse_bench.inference.providers.registry import register_provider
from parse_bench.schemas.parse_output import PageIR, ParseLayoutPageIR, ParseOutput
from parse_bench.schemas.pipeline import PipelineSpec
from parse_bench.schemas.pipeline_io import (
    InferenceRequest,
    InferenceResult,
    RawInferenceResult,
)
from parse_bench.schemas.product import ProductType

_SUPPORTED_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".html", ".xhtml", ".csv", ".adoc"}

# Docling label string → ParseBench layout label string
_LABEL_MAP = {
    "caption": "Caption",
    "footnote": "Footnote",
    "formula": "Formula",
    "list_item": "List-item",
    "page_footer": "Page-footer",
    "page_header": "Page-header",
    "picture": "Picture",
    "chart": "Picture",  # ParseBench has no Chart label; treat as Picture
    "section_header": "Section-header",
    "table": "Table",
    "text": "Text",
    "title": "Title",
    "code": "Text",
    "document_index": "Text",
}


# Per-call holder for force_full_page_ocr flag (read by _build_converter).
# Avoids changing the singleton cache key signature.
_FORCE_FULL_PAGE_OCR_HOLDER: dict = {"value": False}


def _build_converter(ocr_engine: str):
    """Construct a DocumentConverter matching the user's FastAPI service config.

    ocr_engine ∈ {"easyocr", "rapidocr"}. Surya is not natively supported by
    Docling 2.x (would require an external plugin), so it's not accepted here.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.layout_model_specs import DOCLING_LAYOUT_HERON
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        LayoutOptions,
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
        TableStructureOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # force_full_page_ocr is configurable per-pipeline — default False so
    # Docling reuses embedded PDF text when present (only OCRs bitmap clusters).
    # Set True via pipeline config to OCR every page even when text is selectable
    # — useful for a real OCR-engine comparison on this dataset, which mostly
    # has post-OCR'd embedded text layers.
    force_full = bool(_FORCE_FULL_PAGE_OCR_HOLDER.get("value", False))

    if ocr_engine == "rapidocr":
        ocr_options = RapidOcrOptions(force_full_page_ocr=force_full)
    elif ocr_engine == "easyocr":
        ocr_options = EasyOcrOptions(force_full_page_ocr=force_full)
    elif ocr_engine == "tesseract":
        ocr_options = TesseractCliOcrOptions(force_full_page_ocr=force_full)
    else:
        raise ValueError(
            f"Unsupported ocr_engine={ocr_engine!r}; use 'easyocr', 'rapidocr', or 'tesseract'."
        )

    layout_options = LayoutOptions(
        create_orphan_clusters=True,
        model_spec=DOCLING_LAYOUT_HERON,
    )
    table_structure_options = TableStructureOptions(
        mode=TableFormerMode.ACCURATE,
        do_cell_matching=True,
    )
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        allow_external_plugins=True,
        ocr_options=ocr_options,
        do_table_structure=True,
        table_structure_options=table_structure_options,
        layout_options=layout_options,
        do_formula_enrichment=False,
        do_code_enrichment=False,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


class _DoclingSingleton:
    """One DocumentConverter per (engine, force_full_page_ocr) combo."""

    _converters: dict[tuple, Any] = {}

    @classmethod
    def get(cls, ocr_engine: str, force_full_page_ocr: bool = False):
        key = (ocr_engine, force_full_page_ocr)
        if key not in cls._converters:
            _FORCE_FULL_PAGE_OCR_HOLDER["value"] = force_full_page_ocr
            cls._converters[key] = _build_converter(ocr_engine)
        return cls._converters[key]


def _bbox_to_topleft_pixels(bbox: Any, page_height: float) -> tuple[float, float, float, float] | None:
    """Return (x1, y1, x2, y2) in top-left-origin pixel coords for a Docling bbox."""
    try:
        l = float(bbox.l)
        t = float(bbox.t)
        r = float(bbox.r)
        b = float(bbox.b)
    except (AttributeError, TypeError):
        return None
    origin = getattr(bbox, "coord_origin", None)
    origin_val = getattr(origin, "value", origin)
    if origin_val == "BOTTOMLEFT":
        # Convert to top-left: y_top = page_h - y_bottom_top, y_bot = page_h - y_bottom_bot
        y1 = page_height - t
        y2 = page_height - b
    else:
        y1, y2 = t, b
    # Ensure y1 < y2 (top < bottom in TOPLEFT)
    if y1 > y2:
        y1, y2 = y2, y1
    if l > r:
        l, r = r, l
    return l, y1, r, y2


@register_provider("custom_docling")
class CustomDoclingProvider(Provider):
    """Local Docling → markdown + HTML tables + per-element bboxes."""

    def __init__(self, provider_name: str, base_config: dict[str, Any]):
        super().__init__(provider_name=provider_name, base_config=base_config)
        self._ocr_engine: str = base_config.get("ocr_engine", "easyocr")
        self._force_full_page_ocr: bool = bool(base_config.get("force_full_page_ocr", False))

    # ------------------------------------------------------------------ #
    # Build per-page markdown + items walking iterate_items()            #
    # ------------------------------------------------------------------ #
    def _build_pages(self, document) -> list[dict[str, Any]]:
        # Page sizes (1-indexed in Docling)
        page_sizes: dict[int, tuple[float, float]] = {}
        for page_no, page in getattr(document, "pages", {}).items():
            size = getattr(page, "size", None)
            if size is not None:
                page_sizes[int(page_no)] = (float(size.width), float(size.height))

        # Items per page, in document order
        pages: dict[int, dict[str, Any]] = {}

        for item, _level in document.iterate_items():
            label = getattr(item, "label", None)
            label_val = getattr(label, "value", label)
            if label_val is None:
                continue

            provs = getattr(item, "prov", None) or []
            if not provs:
                continue
            prov = provs[0]
            page_no = int(getattr(prov, "page_no", 1) or 1)
            w, h = page_sizes.get(page_no, (0.0, 0.0))
            if w <= 0 or h <= 0:
                continue
            tl_bbox = _bbox_to_topleft_pixels(prov.bbox, h)
            if tl_bbox is None:
                continue
            x1, y1, x2, y2 = tl_bbox
            # Normalize to 0-1000 (build_layout_pages convention)
            bbox_1000 = [
                x1 * 1000.0 / w,
                y1 * 1000.0 / h,
                x2 * 1000.0 / w,
                y2 * 1000.0 / h,
            ]

            # Text content: HTML for tables, plain text otherwise
            text = ""
            if label_val == "table" and hasattr(item, "export_to_html"):
                try:
                    text = item.export_to_html(doc=document)
                except TypeError:
                    # Older docling signatures don't take ``doc``
                    text = item.export_to_html()
            elif label_val == "picture":
                caption = ""
                try:
                    caption = item.caption_text(doc=document) or ""
                except Exception:
                    caption = ""
                text = f"[Figure: {caption}]" if caption else "[Figure]"
            else:
                text = getattr(item, "text", "") or ""

            page_dict = pages.setdefault(page_no, {"items": [], "width": w, "height": h})
            page_dict["items"].append(
                {
                    "bbox": bbox_1000,
                    "label": _LABEL_MAP.get(label_val, "Text"),
                    "text": text,
                }
            )

        # Render markdown per page, HTML for tables, headings for titles
        result_pages: list[dict[str, Any]] = []
        for page_no in sorted(page_sizes.keys()):
            page_dict = pages.get(page_no, {"items": [], "width": page_sizes[page_no][0], "height": page_sizes[page_no][1]})
            md_parts: list[str] = []
            for it in page_dict["items"]:
                lbl = it["label"]
                txt = it["text"]
                if not txt:
                    continue
                if lbl == "Title":
                    md_parts.append(f"# {txt}")
                elif lbl == "Section-header":
                    md_parts.append(f"## {txt}")
                elif lbl == "Formula":
                    md_parts.append(f"$$\n{txt}\n$$")
                else:
                    md_parts.append(txt)
            page_markdown = "\n\n".join(md_parts)
            result_pages.append(
                {
                    "page_index": page_no - 1,  # ParseBench is 0-indexed
                    "markdown": page_markdown,
                    "items": page_dict["items"],
                    "width": page_dict["width"],
                    "height": page_dict["height"],
                }
            )
        return result_pages

    # ------------------------------------------------------------------ #
    # Provider interface                                                 #
    # ------------------------------------------------------------------ #
    def run_inference(self, pipeline: PipelineSpec, request: InferenceRequest) -> RawInferenceResult:
        if request.product_type != ProductType.PARSE:
            raise ProviderPermanentError(
                f"CustomDoclingProvider only supports PARSE, got {request.product_type}"
            )
        source_path = Path(request.source_file_path)
        if not source_path.exists():
            raise ProviderPermanentError(f"Source file not found: {source_path}")
        if source_path.suffix.lower() not in _SUPPORTED_EXT:
            raise ProviderPermanentError(
                f"Unsupported extension {source_path.suffix}; supported: {_SUPPORTED_EXT}"
            )

        started_at = datetime.now()
        converter = _DoclingSingleton.get(self._ocr_engine, self._force_full_page_ocr)
        try:
            result = converter.convert(str(source_path))
        except Exception as exc:
            raise ProviderPermanentError(f"Docling conversion failed: {exc}") from exc

        pages = self._build_pages(result.document)
        try:
            num_pages = int(result.document.num_pages())
        except Exception:
            num_pages = len(pages)

        completed_at = datetime.now()
        latency_ms = int((completed_at - started_at).total_seconds() * 1000)

        raw_output = {
            "pages": pages,
            "num_pages": num_pages,
            "model": "docling-2.x-ibm-granite",
            "mode": "docling_local",
            "config": {},
        }
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
            md = page_data.get("markdown", "")
            items = page_data.get("items", [])
            w = page_data.get("width", 0) or 0
            h = page_data.get("height", 0) or 0
            pages.append(PageIR(page_index=page_index, markdown=md))
            page_markdowns.append(md)
            layout_pages.extend(
                build_layout_pages(items, int(w), int(h), md, page_number=page_index + 1)
            )
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
