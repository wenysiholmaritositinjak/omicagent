"""NCBI E-utilities 轻量客户端 (能力1 数据检索 + 能力3 文献上下文 复用).

公开免费 API (https://www.ncbi.nlm.nih.gov/books/NBK25500/):
- esearch: 搜索 GDS(GEO DataSets)/pubmed 等, 返回 id 列表
- esummary: 取记录摘要 (GEO series 的 accession/title/summary/species/平台/样本数)
- efetch: 取全文/摘要 (pubmed abstract)
- elink: 跨库关联 (GDS -> pubmed)
- 速率: 无 API key <=3 req/s, 有 key <=10 req/s. 本客户端带最小间隔与重试.
"""
from __future__ import annotations
import logging
import time
import re
from typing import Optional

import requests

log = logging.getLogger("omicagent.ncbi")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class NCBIClient:
    def __init__(self, api_key: Optional[str] = None, min_interval: float = 0.34):
        # 无 key 限 ~3 req/s (0.34s 间隔); 有 key 可 0.11s
        self.api_key = api_key
        self.min_interval = min_interval if not api_key else 0.11
        self._last = 0.0

    def _params(self, extra: dict) -> dict:
        p = {"retmode": "json"}
        if self.api_key:
            p["api_key"] = self.api_key
        p.update(extra)
        return p

    def _throttle(self):
        dt = time.time() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.time()

    def _get(self, url: str, params: dict, retries: int = 3) -> dict:
        last_err = None
        for i in range(retries):
            self._throttle()
            try:
                r = requests.get(url, params=params, timeout=30)
                if r.status_code == 429:
                    time.sleep(2 ** i)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
                time.sleep(min(2 ** i, 8))
        raise RuntimeError(f"NCBI 请求失败 {url}: {last_err}")

    # ---------- esearch ----------
    def esearch(self, db: str, term: str, retmax: int = 10) -> list[str]:
        """搜索, 返回 id 列表."""
        data = self._get(f"{EUTILS}/esearch.fcgi",
                         self._params({"db": db, "term": term, "retmax": retmax}))
        return data.get("esearchresult", {}).get("idlist", []) or []

    # ---------- esummary ----------
    def esummary(self, db: str, ids: list[str]) -> dict:
        """取记录摘要, 返回 {uid: record}."""
        if not ids:
            return {}
        data = self._get(f"{EUTILS}/esummary.fcgi",
                         self._params({"db": db, "id": ",".join(ids)}))
        return data.get("result", {}) or {}

    # ---------- elink (跨库关联) ----------
    def elink_pubmed(self, dbfrom: str, ids: list[str]) -> dict:
        """从 dbfrom(如 gds) 关联到 pubmed, 返回 {src_id: [pubmed_id...]}."""
        if not ids:
            return {}
        data = self._get(f"{EUTILS}/elink.fcgi",
                         self._params({"dbfrom": dbfrom, "db": "pubmed",
                                       "id": ",".join(ids)}))
        out = {}
        for ls in data.get("linksets", []):
            src = str(ls.get("ids", [""])[0])
            pmids = []
            for ldb in ls.get("linksetdbs", []) or []:
                pmids.extend([str(x) for x in ldb.get("links", [])])
            out[src] = pmids
        return out

    # ---------- efetch (pubmed 摘要) ----------
    def fetch_pubmed_abstract(self, pmid: str) -> str:
        """取 pubmed 摘要 (retmode=text)."""
        self._throttle()
        try:
            r = requests.get(f"{EUTILS}/efetch.fcgi",
                             params=self._params({"db": "pubmed", "id": pmid,
                                                  "rettype": "abstract", "retmode": "text"}),
                             timeout=30)
            r.raise_for_status()
            return r.text.strip()
        except Exception as e:
            log.warning("取 pubmed 摘要失败 %s: %s", pmid, e)
            return ""

    # ---------- 高层: GEO DataSets 检索 ----------
    def search_geo(self, term: str, retmax: int = 10) -> list[dict]:
        """检索 GEO DataSets (db=gds), 返回结构化记录列表.

        每条含: uid, accession(GSExxx), title, summary, type, n_samples,
        platform, species, gpl, pubmed_ids
        """
        ids = self.esearch("gds", term, retmax=retmax)
        if not ids:
            return []
        summ = self.esummary("gds", ids)
        records = []
        real_ids = summ.get("uids", [])
        # 关联 pubmed
        pmid_map = {}
        try:
            pmid_map = self.elink_pubmed("gds", real_ids)
        except Exception as e:
            log.warning("elink 失败: %s", e)
        for uid in real_ids:
            rec = summ[uid]
            acc = rec.get("accession", "")
            records.append({
                "uid": uid,
                "accession": acc,
                "title": rec.get("title", ""),
                "summary": rec.get("summary", ""),
                "type": rec.get("gdstype", rec.get("type", "")),
                "n_samples": int(rec.get("n_samples", 0) or 0),
                "platform": rec.get("gpl", ""),
                "species": _extract_species(rec),
                "pubmed_ids": pmid_map.get(uid, []),
                "entrytype": rec.get("entrytype", ""),
                "gds": rec.get("gds", ""),
            })
        return records


def _extract_species(rec: dict) -> str:
    """从 esummary 记录提取物种名."""
    # GEO esummary 有时把物种放在 taxon/sample 字段; title/summary 里也可能有
    for k in ("taxon", "species", "organism"):
        v = rec.get(k)
        if v:
            return str(v)
    return ""


def geo_suppl_url(accession: str) -> str:
    """构造 GEO series supplementary FTP 目录 URL.

    GSE332675 -> https://ftp.ncbi.nlm.nih.gov/geo/series/GSE332nnn/GSE332675/suppl/
    """
    acc = accession.strip().upper()
    m = re.match(r"(GSE\d+)$", acc)
    if not m:
        return ""
    num = m.group(1)
    prefix = num[:-3] + "nnn"  # GSE332675 -> GSE332nnn
    return f"https://ftp.ncbi.nlm.nih.gov/geo/series/{prefix}/{num}/suppl/"


def _parse_size(s: str) -> int:
    """解析 Apache 列表大小字符串 (118M/1.2G/580/-) 为字节."""
    if not s or s == "-":
        return 0
    s = s.strip()
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    try:
        if s[-1] in units:
            return int(float(s[:-1]) * units[s[-1]])
        return int(float(s))
    except Exception:
        return 0


def _file_type(name: str) -> str:
    """推断文件类型: processed(处理好 rds/h5ad) / matrix(10x矩阵) / raw(测序) / archive / other."""
    n = name.lower()
    if any(n.endswith(e) for e in (".rds", ".h5ad", ".h5", ".loom", ".rds.gz")):
        return "processed"
    if any(n.endswith(e) for e in (".mtx", ".mtx.gz", ".tsv", ".tsv.gz", ".csv", ".csv.gz")):
        return "matrix"
    if any(n.endswith(e) for e in (".fastq", ".fq", ".fastq.gz", ".fq.gz", ".bam")):
        return "raw"
    if ".tar" in n or n.endswith(".zip"):
        return "archive"
    return "other"


def geo_suppl_files(accession: str, timeout: int = 12) -> list[dict]:
    """列 GEO series supplementary 文件, 返回 [{name, size_bytes, size_human, type, url}].

    解析 Apache 目录列表, 一次请求获取文件名+大小+类型.
    """
    import requests as _rq
    url = geo_suppl_url(accession)
    if not url:
        return []
    try:
        r = _rq.get(url, timeout=timeout)
        if r.status_code != 200:
            return []
        r.encoding = "utf-8"
    except Exception:
        return []
    # Apache 列表行: <a href="name">name</a>   YYYY-MM-DD HH:MM  size
    pattern = re.compile(r'<a href="([^"]+)">[^<]+</a>\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+([\d.]+[KMGT]?|-)')
    files = []
    for m in pattern.finditer(r.text):
        name = m.group(1)
        if name.startswith("/") or name.startswith("?") or name == "Parent Directory":
            continue
        size_bytes = _parse_size(m.group(3))
        files.append({
            "name": name,
            "size_bytes": size_bytes,
            "size_human": _human_size(size_bytes),
            "type": _file_type(name),
            "url": url + name,
        })
    return files


def _human_size(n: int) -> str:
    """字节转人类可读 (118M, 1.2G)."""
    if n <= 0:
        return "-"
    for u in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.1f}{u}" if u != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}P"
