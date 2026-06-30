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

# 非细胞类型词, 过滤 (LLM 偶抽 "Unknown"/"NA" 等)
BLACKLIST = {"unknown", "na", "nan", "unannotated", "unassigned", "other",
             "low quality", "doublet", "debris", "uncategorized"}

EXTRACT_PROMPT = """你是植物单细胞细胞类型注释抽取助手. 从下面文献片段抽取文中明确出现的细胞类型注释.

输出规则 (严格):
- 只输出一个 JSON 数组, 不要任何解释文字, 不要 markdown 代码围栏
- 数组每个元素: 一个对象, 字段如下
  raw_label: 文中细胞类型原文名(英文, 原样)
  raw_label_zh: 中文名(无则空字符串)
  marker_genes: 该类型 marker 基因, 英文逗号分隔(无则空字符串)
  tissue: leaf/root/stem/seed/flower/meristem/other — 该类型实际所在组织; 优先用文献主组织(见上方), 仅当该类型明确属另一组织时才改
  source_type: methods_table/results_text/figure_legend
  evidence: 原文中提及该细胞类型的完整句子(<=150字, 英文原文, 主谓结构, 可溯源); 避免纯图例编号列表(如"1 XX 2 YY")
  confidence: high(文献显式标注) / mid(推测) / low
- 只抽文中明确写出的细胞类型名, 不要臆造, 不要把器官/组织名当细胞类型
- 同一细胞类型只输出一次 (marker_genes 合并所有出现)
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
        try:
            pt = extract_text(pdf_path)
            secs = _select_sections(pt, self.max_input_chars)
            tables = _select_tables(extract_tables(pdf_path, max_pages=25))
        except Exception as e:
            # pymupdf 偶发崩溃 (扫描版/加密/大文件), 单篇失败不拖垮整批
            log.warning("PDF 抽取失败 %s: %s", paper.get("title", "")[:30], e)
            return []
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
        seen: dict[str, CorpusRow] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            raw = (it.get("raw_label") or "").strip()
            if not raw:
                continue
            if raw.lower() in BLACKLIST:
                continue
            mk = it.get("marker_genes", "") or ""
            if raw in seen:
                # 去重: 同 raw_label 合并 marker (首次为空则补)
                if mk and not seen[raw].marker_genes:
                    seen[raw].marker_genes = mk
                continue
            # species/tissue: 优先 LLM 单值, 否则 guess 取首值 (去笛卡尔积串污染)
            sp = (it.get("species") or "").strip()
            if not sp:
                sp = (paper.get("species_guess") or "").split(",")[0].strip()
            ti = (it.get("tissue") or "").strip()
            if not ti:
                ti = (paper.get("tissue_guess") or "").split(",")[0].strip()
            seen[raw] = CorpusRow(
                paper_id=paper.get("item_id", ""),
                paper_title=paper.get("title", "")[:60],
                doi=paper.get("doi", ""),
                pmid="",
                species=sp,
                tissue=ti,
                raw_label=raw,
                raw_label_zh=it.get("raw_label_zh", "") or "",
                marker_genes=mk,
                n_cells="",
                source_type=it.get("source_type", "") or "",
                evidence=(it.get("evidence") or "")[:200],
                confidence=it.get("confidence", "mid") or "mid",
                harvested_at=ts,
            )
        return list(seen.values())

    def harvest_batch(self, papers: list[dict], out_csv: str,
                      max_papers: int = 0, resume: bool = True) -> list[CorpusRow]:
        """批量采集, 增量写 corpus.csv (每篇完成即写盘, 支持断点续跑).

        resume=True: 跳过 out_csv 中已有 paper_id 的篇目 (按标题去重), 适合 WSL 崩溃后续跑.
        """
        done_ids: set[str] = set()
        existing: list[CorpusRow] = []
        if resume and os.path.exists(out_csv):
            existing = self._read_csv(out_csv)
            done_ids = {r.paper_id for r in existing}
            log.info("续跑: 已完成 %d 篇 (%d 行), 跳过", len(done_ids), len(existing))
        out_rows = list(existing)
        n = len(papers) if max_papers <= 0 else min(max_papers, len(papers))
        for i, p in enumerate(papers[:n], 1):
            pid = p.get("item_id", "")
            if pid and pid in done_ids:
                continue
            log.info("[%d/%d] %s", i, n, p.get("title", "")[:40])
            rows = self.harvest_paper(p)
            if rows:
                out_rows.extend(rows)
                self._write_csv(out_rows, out_csv)  # 每篇完成即写盘 (增量)
                log.info("已写 %d 行 -> %s", len(out_rows), out_csv)
        log.info("完成: %d 行 -> %s", len(out_rows), out_csv)
        return out_rows

    @staticmethod
    def _write_csv(rows, out_csv):
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(CorpusRow.csv_header())
            for r in rows:
                w.writerow(r.to_csv_row())

    @staticmethod
    def _read_csv(out_csv) -> list[CorpusRow]:
        """读已有 corpus.csv (续跑用)."""
        import csv as _csv
        rows: list[CorpusRow] = []
        if not os.path.exists(out_csv):
            return rows
        with open(out_csv, encoding="utf-8", newline="") as f:
            r = _csv.DictReader(f)
            for d in r:
                rows.append(CorpusRow(**{k: d.get(k, "") for k in CorpusRow.csv_header()}))
        return rows
