"""注释采集器 (annotation_harvester).

从 Zotero 文献 PDF 批量抽取细胞类型注释 -> 写 corpus.csv.

流程: zotero_index.json -> 筛 v1 子集 -> 每篇 pdf_extractor 抽注释段+表格
      -> 段落筛选(强信号优先, 控 token) -> LLM 抽 raw_label+marker+evidence
      -> CorpusRow -> corpus.csv

映射目标: scPlantDB plant_sc 本体 (P2 用 map_to_standard 映射).
单篇注释段落 10-17 万字符, 须段落筛选+分批, 单次 LLM 输入控 <25K 字符 (~8K token).
"""
from __future__ import annotations
import csv, logging, os
from datetime import datetime
from pathlib import Path

from ..llm_client import LLMClient
from .schemas import CorpusRow
from .pdf_extractor import (extract_text, extract_tables, find_annotation_sections)

log = logging.getLogger("omicagent.annotation.harvester")

# 强信号关键词: 段落含这些才优先送 LLM (控 token + 提精度)
STRONG_KEYWORDS = ("cell type", "cell types", "cluster", "annotated", "annotation",
                   "marker gene", "marker genes", "identified as", "assigned",
                   "named as", "classified as")
MAX_INPUT_CHARS = 12000  # 单次 LLM 输入字符上限 (减输入, 留 token 给 glm-5.2 思考+输出)

EXTRACT_PROMPT = """你是植物单细胞细胞类型注释抽取助手. 从下面文献片段抽取文中明确出现的细胞类型注释.

输出规则 (严格):
- 只输出一个 JSON 数组, 不要任何解释文字, 不要 markdown 代码围栏
- 数组每个元素: 一个对象, 字段如下
  raw_label: 文中细胞类型原文名(英文, 原样)
  raw_label_zh: 中文名(无则空字符串)
  marker_genes: 该类型 marker 基因, 英文逗号分隔(无则空字符串)
  tissue: leaf/root/stem/seed/flower/meristem/other (从全文判断)
  source_type: methods_table/results_text/figure_legend
  evidence: 原文中提及该细胞类型的句子(<=150字, 英文原文, 可溯源)
  confidence: high(文献显式标注) / mid(推测) / low
- 只抽文中明确写出的细胞类型名, 不要臆造, 不要把器官/组织名当细胞类型
- evidence 必须是原文片段

文献标题: __TITLE__
物种(从标题/全文判断): __SPECIES__
组织(从标题/全文判断): __TISSUE__

--- 注释段落 ---
__SECTIONS__

--- 表格 ---
__TABLES__
"""


def _select_sections(pdf_text, max_chars=MAX_INPUT_CHARS):
    """从注释段落里按强信号评分筛选, 控制总字符."""
    secs = find_annotation_sections(pdf_text)

    def score(s):
        low = s.text.lower()
        return sum(1 for k in STRONG_KEYWORDS if k in low)
    secs.sort(key=score, reverse=True)
    out, total = [], 0
    for s in secs:
        if total + len(s.text) > max_chars:
            continue
        out.append(s)
        total += len(s.text)
        if total >= max_chars * 0.8:
            break
    return out


def _select_tables(tables, max_chars=8000):
    """筛选表头含 celltype/marker/cluster 的表格."""
    out, total = [], 0
    for t in tables:
        if t.n_rows < 2:
            continue
        head = " ".join(t.rows[0]).lower() if t.rows else ""
        if not any(k in head for k in ("cell type", "cell", "cluster", "marker", "annotation", "type")):
            continue
        text = "\n".join(" | ".join(r) for r in t.rows[:30])
        if total + len(text) > max_chars:
            continue
        out.append((t.page, text))
        total += len(text)
    return out


class AnnotationHarvester:
    def __init__(self, llm=None, max_input_chars=MAX_INPUT_CHARS):
        self.llm = llm or LLMClient()
        self.max_input_chars = max_input_chars

    def harvest_paper(self, paper: dict) -> list[CorpusRow]:
        """从一篇文献 (zotero_index 的 paper dict) 抽 CorpusRow 列表."""
        pdf_path = paper.get("pdf_path", "")
        if not pdf_path or not os.path.exists(pdf_path):
            log.warning("PDF 不存在: %s", pdf_path)
            return []
        pt = extract_text(pdf_path)
        secs = _select_sections(pt, self.max_input_chars)
        tables = _select_tables(extract_tables(pdf_path, max_pages=25))
        if not secs and not tables:
            log.info("无注释内容: %s", paper.get("title", "")[:40])
            return []

        sections_text = "\n\n".join(f"[p{s.page}] {s.text}" for s in secs[:8]) or "(无)"
        tables_text = "\n\n".join(f"[p{pg} table]\n{txt}" for pg, txt in tables[:5]) or "(无表格)"
        prompt = (EXTRACT_PROMPT
                  .replace("__TITLE__", paper.get("title", "")[:120])
                  .replace("__SPECIES__", paper.get("species_guess", "") or "(未知)")
                  .replace("__TISSUE__", paper.get("tissue_guess", "") or "(未知)")
                  .replace("__SECTIONS__", sections_text)
                  .replace("__TABLES__", tables_text))
        try:
            data = self.llm.complete_json(prompt, task_type="complex", max_tokens=8192)
        except Exception as e:
            log.warning("LLM 抽取失败 %s: %s", paper.get("title", "")[:30], e)
            return []
        rows = self._parse(data, paper)
        log.info("抽取 %s: %d 条", paper.get("title", "")[:30], len(rows))
        return rows

    @staticmethod
    def _parse(data, paper) -> list[CorpusRow]:
        """兼容 LLM 输出: list / dict(含 list 值) / 嵌套."""
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    items = v
                    break
        ts = datetime.now().isoformat(timespec="seconds")
        rows = []
        for it in items:
            if not isinstance(it, dict):
                continue
            raw = (it.get("raw_label") or "").strip()
            if not raw:
                continue
            rows.append(CorpusRow(
                paper_id=paper.get("item_id", ""),
                paper_title=paper.get("title", "")[:60],
                doi=paper.get("doi", ""),
                pmid="",
                species=(it.get("species") or paper.get("species_guess") or ""),
                tissue=(it.get("tissue") or paper.get("tissue_guess") or ""),
                raw_label=raw,
                raw_label_zh=it.get("raw_label_zh", "") or "",
                marker_genes=it.get("marker_genes", "") or "",
                n_cells="",
                source_type=it.get("source_type", "") or "",
                evidence=(it.get("evidence") or "")[:200],
                confidence=it.get("confidence", "mid") or "mid",
                harvested_at=ts,
            ))
        return rows

    def harvest_batch(self, papers: list[dict], out_csv: str,
                      max_papers: int = 0) -> list[CorpusRow]:
        """批量采集, 写 corpus.csv."""
        all_rows: list[CorpusRow] = []
        n = len(papers) if max_papers <= 0 else min(max_papers, len(papers))
        for i, p in enumerate(papers[:n], 1):
            log.info("[%d/%d] %s", i, n, p.get("title", "")[:40])
            rows = self.harvest_paper(p)
            all_rows.extend(rows)
        self._write_csv(all_rows, out_csv)
        log.info("完成: %d 篇 -> %d 行 -> %s", n, len(all_rows), out_csv)
        return all_rows

    @staticmethod
    def _write_csv(rows, out_csv):
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(CorpusRow.csv_header())
            for r in rows:
                w.writerow(r.to_csv_row())
