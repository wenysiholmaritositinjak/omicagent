"""PDF 文本与注释段落抽取 (pymupdf).

从文献 PDF 抽:
- 全文文本 (按页)
- 表格 (pymupdf find_tables, 供 marker 表 / cell type 表抽取)
- 注释相关段落 (含 cell type annotation / clustering / marker 等关键词的段落)

P0 出 extract_text / extract_tables / find_annotation_sections 三个基础函数;
LLM 从段落+表格抽 raw_label 放 P1 (annotation_harvester).
"""
from __future__ import annotations
import logging, re
from dataclasses import dataclass, field

log = logging.getLogger("omicagent.annotation.pdf")

# 注释相关关键词 (小写匹配). 命中任一即视为注释段落候选
ANNOTATION_KEYWORDS = (
    "cell type", "cell types", "cell-type", "cell identity", "cell identities",
    "annotation", "annotated", "annotate",
    "cluster", "clusters", "clustering", "subcluster",
    "marker gene", "marker genes", "markers", "differentially expressed",
    "classified as", "identified as", "assigned as", "assigned to",
    "named as", "labeled", "labelled",
    "guard cell", "mesophyll", "epidermis", "vascular", "xylem", "phloem",
    "parenchyma", "meristem", "progenitor",
)
# 段落切分: 按双换行
_PARA_SPLIT = re.compile(r"\n{2,}")


@dataclass
class PdfText:
    pdf_path: str
    n_pages: int
    pages: list[dict] = field(default_factory=list)  # [{page:int, text:str}]

    @property
    def full_text(self) -> str:
        return "\n\n".join(p["text"] for p in self.pages)


@dataclass
class TableInfo:
    page: int
    table_index: int
    n_rows: int
    n_cols: int
    rows: list[list[str]]


@dataclass
class AnnotationSection:
    page: int
    text: str
    matched_keywords: list[str]


def extract_text(pdf_path: str, max_pages: int = 0) -> PdfText:
    """抽 PDF 全文文本 (按页). max_pages=0 表示全部."""
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    n = doc.page_count
    limit = n if max_pages <= 0 else min(max_pages, n)
    for i in range(limit):
        text = doc[i].get_text("text") or ""
        pages.append({"page": i + 1, "text": text})
    doc.close()
    return PdfText(pdf_path=str(pdf_path), n_pages=n, pages=pages)


def extract_tables(pdf_path: str, max_pages: int = 0) -> list[TableInfo]:
    """抽 PDF 表格 (pymupdf find_tables). 供 marker / cell type 表抽取."""
    import fitz
    out: list[TableInfo] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        log.warning("打开 PDF 失败 %s: %s", pdf_path, e)
        return out
    n = doc.page_count
    limit = n if max_pages <= 0 else min(max_pages, n)
    for i in range(limit):
        try:
            tabs = doc[i].find_tables()
        except Exception:
            continue
        for ti, tab in enumerate(tabs):
            try:
                rows = [[str(c) if c is not None else "" for c in row]
                        for row in tab.extract()]
            except Exception:
                continue
            if not rows:
                continue
            out.append(TableInfo(page=i + 1, table_index=ti,
                                 n_rows=len(rows),
                                 n_cols=len(rows[0]) if rows else 0,
                                 rows=rows))
    doc.close()
    return out


def find_annotation_sections(pdf_text: PdfText) -> list[AnnotationSection]:
    """从全文找注释相关段落 (含关键词的段落)."""
    out: list[AnnotationSection] = []
    for pg in pdf_text.pages:
        text = pg["text"]
        if not text.strip():
            continue
        paras = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
        # 短段落合并 (PDF 抽文本常把一行切成一段, 合到 >=80 字符以便含上下文)
        merged: list[str] = []
        buf = ""
        for p in paras:
            if len(buf) < 80:
                buf = (buf + " " + p).strip()
            else:
                merged.append(buf)
                buf = p
        if buf:
            merged.append(buf)
        for para in merged:
            low = para.lower()
            hits = [kw for kw in ANNOTATION_KEYWORDS if kw in low]
            if hits and len(para) >= 30:
                out.append(AnnotationSection(page=pg["page"], text=para,
                                             matched_keywords=hits))
    return out


def has_annotation_content(pdf_path: str) -> bool:
    """快速判断 PDF 是否含注释内容 (前 8 页有关键词段落)."""
    try:
        pt = extract_text(pdf_path, max_pages=8)
    except Exception:
        return False
    return bool(find_annotation_sections(pt))
