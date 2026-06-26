"""能力1: 智能文献与数据检索 (Data Searcher).

多源检索器架构: 自然语言需求 -> LLM 解析查询 -> 路由到各数据库检索器
-> 去重 + LLM 重排 -> 返回文献列表与数据下载链接.

主通道: NCBI GEO (E-utilities, 公开真实可用).
辅通道: ArrayExpress / cellxgene Census / OmicSeek (尽力而为, 不可达优雅跳过).
灵活路由: 需求/文献中明确提到数据上传到某库时, 路由到对应检索器.
"""
from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

from . import config
from .llm_client import LLMClient
from .ncbi_client import NCBIClient, geo_suppl_url, geo_suppl_files

log = logging.getLogger("omicagent.search")


@dataclass
class DatasetRecord:
    title: str
    accession: str = ""
    source_db: str = ""
    species: str = ""
    platform: str = ""
    n_samples: int = 0
    modality: str = ""
    summary: str = ""
    download_url: str = ""
    paper_doi: str = ""
    pubmed_id: str = ""
    metadata: dict = field(default_factory=dict)
    relevance: float = 0.0  # 重排打分
    files: list = field(default_factory=list)        # suppl 文件详情 [{name,size_bytes,size_human,type,url}]
    data_type: str = ""                               # processed/matrix/raw/mixed/unknown
    total_size_bytes: int = 0                         # 全部文件总大小
    has_processed: bool = False                       # 是否有处理好的 rds/h5ad

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedQuery:
    """LLM 从自然语言需求解析出的结构化查询."""
    keywords: list[str] = field(default_factory=list)
    species: str = ""
    modality: str = ""          # scRNA-seq / snRNA-seq / spatial / ...
    tissue: str = ""            # leaf / root / ...
    celltype_hint: str = ""     # 气孔细胞 etc
    source_hints: list[str] = field(default_factory=list)  # 文献提到的库: geo/arrayexpress/...
    geo_term: str = ""          # 构造好的 GEO 检索式


@dataclass
class SearchReport:
    query: str
    parsed: dict
    records: list[DatasetRecord]
    elapsed: float
    sources_tried: list[str]
    sources_ok: list[str]

    def to_dict(self) -> dict:
        return {
            "query": self.query, "parsed": self.parsed,
            "records": [r.to_dict() for r in self.records],
            "elapsed": round(self.elapsed, 2),
            "sources_tried": self.sources_tried, "sources_ok": self.sources_ok,
            "n_records": len(self.records),
        }


# ===================== 检索器基类 =====================
class BaseSearcher:
    name: str = "base"
    def search(self, pq: ParsedQuery, topk: int = 5) -> list[DatasetRecord]:
        raise NotImplementedError
    def can_handle(self, source_hint: str) -> bool:
        return source_hint.lower() == self.name
    def available(self) -> bool:
        return True


# ===================== NCBI GEO (核心) =====================
class NCBIGeoSearcher(BaseSearcher):
    name = "geo"
    def __init__(self, ncbi: Optional[NCBIClient] = None):
        self.ncbi = ncbi or NCBIClient(api_key=getattr(config, "NCBI_API_KEY", None))

    def search(self, pq: ParsedQuery, topk: int = 5) -> list[DatasetRecord]:
        term = pq.geo_term or self._build_term(pq)
        log.info("[GEO] 检索: %s", term)
        recs = self.ncbi.search_geo(term, retmax=topk)
        out = []
        for r in recs:
            acc = r.get("accession", "")
            # 查 suppl 文件详情 (类型/大小)
            files, data_type, total_sz, has_proc = [], "", 0, False
            if acc:
                try:
                    files = geo_suppl_files(acc, timeout=10)
                    types = {f["type"] for f in files}
                    total_sz = sum(f["size_bytes"] for f in files)
                    has_proc = "processed" in types
                    if has_proc:
                        data_type = "processed" if not (types - {"processed", "other"}) else "mixed"
                    elif "matrix" in types:
                        data_type = "matrix"
                    elif "raw" in types:
                        data_type = "raw"
                    elif "archive" in types:
                        data_type = "archive"
                    else:
                        data_type = "unknown" if not files else "other"
                except Exception as e:
                    log.debug("取 suppl 文件失败 %s: %s", acc, e)
            out.append(DatasetRecord(
                title=r.get("title", ""),
                accession=acc,
                source_db="GEO",
                species=r.get("species", ""),
                platform=r.get("platform", ""),
                n_samples=r.get("n_samples", 0),
                modality=_guess_modality(r.get("summary", "") + " " + r.get("title", "")),
                summary=r.get("summary", ""),
                download_url=geo_suppl_url(acc),
                pubmed_id=";".join(r.get("pubmed_ids", [])),
                metadata={"uid": r.get("uid", ""), "type": r.get("type", ""),
                          "gpl": r.get("platform", "")},
                files=files, data_type=data_type,
                total_size_bytes=total_sz, has_processed=has_proc,
            ))
        return out

    def _build_term(self, pq: ParsedQuery) -> str:
        """从 ParsedQuery 构造 GEO 检索式.

        只用 species+modality+tissue+EntryType 做 AND (避免关键词过严导致 0 结果);
        keywords 不并入检索式 (常与前三者重复且带空格短语匹配差), 留给 LLM 重排打分.
        """
        parts = []
        if pq.species:
            parts.append(f'"{pq.species}"[Organism]')
        if pq.modality:
            parts.append(f'({pq.modality}[All Fields] OR single cell[All Fields])')
        else:
            parts.append('(single cell[All Fields] OR scRNA[All Fields] OR snRNA[All Fields])')
        if pq.tissue:
            t = "".join(c for c in pq.tissue if ord(c) < 128).strip()
            if t:
                parts.append(f'{t}[All Fields]')
        # species/tissue 都空时, 用首个 ASCII keyword 兜底
        if not pq.species and not pq.tissue:
            for kw in pq.keywords:
                kw_ascii = "".join(c for c in kw if ord(c) < 128).strip()
                if 2 < len(kw_ascii) < 40:
                    parts.append(f'{kw_ascii}[All Fields]')
                    break
        parts.append('"gse"[Entry Type]')
        return " AND ".join(parts) if parts else "single cell[All Fields]"


# ===================== ArrayExpress (EBI, 尽力而为) =====================
class ArrayExpressSearcher(BaseSearcher):
    name = "arrayexpress"
    BASE = "https://www.ebi.ac.uk/arrayexpress/json/v3/search"
    def search(self, pq: ParsedQuery, topk: int = 5) -> list[DatasetRecord]:
        q = " ".join([pq.species, pq.modality, pq.tissue] + pq.keywords).strip() or "single cell"
        try:
            r = requests.get(self.BASE, params={"q": q, "limit": topk, "format": "json"},
                             timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.info("[ArrayExpress] 不可用: %s", e)
            return []
        out = []
        for exp in data.get("experiments", {}).get("experiment", []) or []:
            out.append(DatasetRecord(
                title=exp.get("experiment", {}).get("title", ""),
                accession=exp.get("accession", ""),
                source_db="ArrayExpress",
                species=exp.get("experiment", {}).get("species", ""),
                n_samples=int(exp.get("experiment", {}).get("samplecount", 0) or 0),
                summary=exp.get("experiment", {}).get("description", ""),
                download_url=f"https://www.ebi.ac.uk/arrayexpress/experiments/{exp.get('accession','')}/",
                metadata={"releasedate": exp.get("experiment", {}).get("releasedate", "")},
            ))
        return out

    def available(self) -> bool:
        try:
            requests.get(self.BASE, params={"q": "test", "limit": 1}, timeout=8)
            return True
        except Exception:
            return False


# ===================== OmicSeek (保留入口) =====================
class OmicSeekSearcher(BaseSearcher):
    name = "omicseek"
    def __init__(self):
        self.base = getattr(config, "OMICSEEK_BASE", "")
    def search(self, pq: ParsedQuery, topk: int = 5) -> list[DatasetRecord]:
        if not self.base:
            return []
        try:
            r = requests.get(f"{self.base}/api/search",
                             params={"q": " ".join(pq.keywords) or pq.species, "topk": topk},
                             timeout=15)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception as e:
            log.info("[OmicSeek] 不可用: %s", e)
            return []
        out = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            out.append(DatasetRecord(
                title=item.get("title", ""), accession=item.get("accession", ""),
                source_db="OmicSeek", species=item.get("species", ""),
                summary=item.get("summary", ""), download_url=item.get("download_url", ""),
            ))
        return out
    def available(self) -> bool:
        return bool(self.base)


# ===================== 主类: DataSearcher =====================
class DataSearcher:
    def __init__(self, llm: Optional[LLMClient] = None, searchers: Optional[list[BaseSearcher]] = None):
        self.llm = llm or LLMClient()
        self.ncbi = NCBIClient(api_key=getattr(config, "NCBI_API_KEY", None))
        if searchers is None:
            searchers = [NCBIGeoSearcher(self.ncbi), ArrayExpressSearcher(), OmicSeekSearcher()]
        self.searchers = {s.name: s for s in searchers}

    # ---------- 自然语言 -> ParsedQuery ----------
    def parse_query(self, user_query: str) -> ParsedQuery:
        prompt = (
            "你是组学数据检索助手. 把用户自然语言需求解析为结构化检索查询, 输出 JSON:\n"
            '{"keywords":[...英文关键词, 用于NCBI检索, 如"guard cell"/"stomata"], '
            '"species":"(拉丁学名如 Arabidopsis thaliana, 留空若未指定)", '
            '"modality":"(scRNA-seq/snRNA-seq/spatial RNA-seq 等)", "tissue":"(英文如 leaf/root)", '
            '"celltype_hint":"(英文细胞类型如 guard cell, 无则空)", '
            '"source_hints":["geo"/"arrayexpress"/"omicseek"/"cellxgene" 等用户明确提到的库, 无则空数组]}\n'
            "注意: keywords/tissue/celltype_hint 必须用英文(GEO不支持中文检索), 物种用拉丁学名.\n"
            f"用户需求: {user_query}\n只输出 JSON."
        )
        data = self.llm.complete_json(prompt, task_type="complex", max_tokens=800)
        pq = ParsedQuery(
            keywords=data.get("keywords", []) or [],
            species=data.get("species", "") or "",
            modality=data.get("modality", "") or "",
            tissue=data.get("tissue", "") or "",
            celltype_hint=data.get("celltype_hint", "") or "",
            source_hints=[s.lower() for s in (data.get("source_hints") or [])],
        )
        log.info("解析查询: species=%s modality=%s tissue=%s hints=%s",
                 pq.species, pq.modality, pq.tissue, pq.source_hints)
        return pq

    # ---------- 主检索 ----------
    def search(self, user_query: str, topk: int = 5) -> SearchReport:
        t0 = time.time()
        pq = self.parse_query(user_query)

        # 决定调用哪些检索器: source_hints 优先 + 默认 geo
        to_use = []
        for hint in pq.source_hints:
            if hint in self.searchers and self.searchers[hint] not in to_use:
                to_use.append(self.searchers[hint])
        if self.searchers["geo"] not in to_use:
            to_use.append(self.searchers["geo"])  # GEO 始终作为主通道
        # 若无 hint, 也尝试 ArrayExpress(若可用)
        if not pq.source_hints and "arrayexpress" in self.searchers:
            to_use.append(self.searchers["arrayexpress"])

        sources_tried = [s.name for s in to_use]
        all_recs: list[DatasetRecord] = []
        sources_ok = []

        # 并行调用各检索器
        with ThreadPoolExecutor(max_workers=min(4, len(to_use))) as ex:
            fut = {ex.submit(s.search, pq, topk): s for s in to_use}
            for f in as_completed(fut):
                s = fut[f]
                try:
                    recs = f.result()
                    if recs:
                        sources_ok.append(s.name)
                        all_recs.extend(recs)
                        log.info("[%s] 返回 %d 条", s.name, len(recs))
                except Exception as e:
                    log.warning("[%s] 检索失败: %s", s.name, e)

        # 去重 (按 accession, 空 accession 保留)
        seen, dedup = set(), []
        for r in all_recs:
            key = r.accession or f"{r.source_db}:{r.title[:40]}"
            if key in seen:
                continue
            seen.add(key)
            dedup.append(r)

        # LLM 重排打分 (相关性)
        ranked = self._rerank(user_query, pq, dedup, topk)

        return SearchReport(
            query=user_query,
            parsed={"keywords": pq.keywords, "species": pq.species, "modality": pq.modality,
                    "tissue": pq.tissue, "celltype_hint": pq.celltype_hint,
                    "source_hints": pq.source_hints},
            records=ranked,
            elapsed=time.time() - t0,
            sources_tried=sources_tried,
            sources_ok=sources_ok,
        )

    def _rerank(self, user_query: str, pq: ParsedQuery, recs: list[DatasetRecord],
                topk: int) -> list[DatasetRecord]:
        if not recs:
            return []
        # 简要列表送 LLM 打分
        brief = [{"i": i, "acc": r.accession, "title": r.title[:120],
                  "species": r.species, "summary": r.summary[:200]}
                 for i, r in enumerate(recs)]
        prompt = (
            "用户需求: " + user_query + "\n"
            f"候选数据集(共{len(brief)}): " + __import__('json').dumps(brief, ensure_ascii=False) + "\n"
            "按与需求相关性打分(0-1), 输出 JSON 数组 [{\"i\":0,\"score\":0.9}, ...]. 只输出 JSON 数组."
        )
        try:
            scores = self.llm.complete_json(prompt, task_type="complex", max_tokens=1200)
            smap = {item["i"]: float(item.get("score", 0)) for item in scores} if isinstance(scores, list) else {}
            for i, r in enumerate(recs):
                r.relevance = smap.get(i, 0.0)
            recs.sort(key=lambda x: x.relevance, reverse=True)
        except Exception as e:
            log.warning("重排打分失败, 保留原序: %s", e)
        return recs[:topk]

    # ---------- 下载 ----------
    def download(self, record: DatasetRecord, dest_dir: str = "~/bioinfo/data/downloaded") -> list[str]:
        """下载某数据集的 supplementary 文件 (列 FTP 目录 -> 抓文件)."""
        import os, subprocess
        from pathlib import Path
        d = Path(os.path.expanduser(dest_dir)) / (record.accession or "unknown")
        d.mkdir(parents=True, exist_ok=True)
        url = record.download_url
        if not url:
            return []
        files = []
        if record.source_db == "GEO":
            # 列 FTP 目录, 抓所有文件
            try:
                r = requests.get(url, timeout=30)
                import re
                names = re.findall(r'href="([^"]+\.(?:h5ad|rds|mtx|tar\.gz|tsv|csv|gz))"', r.text, re.I)
                for n in names:
                    furl = url + n
                    out = d / n
                    subprocess.run(["curl", "-sL", "-o", str(out), furl], check=False, timeout=1800)
                    if out.exists() and out.stat().st_size > 0:
                        files.append(str(out))
            except Exception as e:
                log.warning("GEO 下载失败: %s", e)
        else:
            # 直接下载链接
            out = d / "data"
            subprocess.run(["curl", "-sL", "-o", str(out), url], check=False, timeout=1800)
            if out.exists() and out.stat().st_size > 0:
                files.append(str(out))
        return files


def _guess_modality(text: str) -> str:
    t = text.lower()
    if "spatial" in t:
        return "spatial RNA-seq"
    if "sn-rna" in t or "snrna" in t or "single-nucleus" in t:
        return "snRNA-seq"
    if "sc-rna" in t or "scrna" in t or "single-cell" in t or "single cell" in t:
        return "scRNA-seq"
    return ""
