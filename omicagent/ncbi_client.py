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
    def __init__(self, api_key: Optional[str] = None, min_interval: float = 0.4):
        # 无 key 限 ~2.5 req/s (0.4s 间隔, 留余量避 429); 有 key 可 0.12s
        self.api_key = api_key
        self.min_interval = min_interval if not api_key else 0.12
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
            r.encoding = "utf-8"
            return r.text.strip()
        except Exception as e:
            log.warning("取 pubmed 摘要失败 %s: %s", pmid, e)
            return ""

    def fetch_pubmed_fulltext(self, pmid: str) -> str:
        """取 pubmed 完整记录 (含摘要 + 数据可用性声明, retmode=xml 解析纯文本)."""
        self._throttle()
        try:
            r = requests.get(f"{EUTILS}/efetch.fcgi",
                             params=self._params({"db": "pubmed", "id": pmid,
                                                  "rettype": "abstract", "retmode": "xml"}),
                             timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"
            # 粗解析 XML: 去 tag 取文本 (含 AbstractText + AccessionNumberList 等)
            import re as _re
            txt = _re.sub(r"<[^>]+>", " ", r.text)
            txt = _re.sub(r"\s+", " ", txt).strip()
            return txt
        except Exception as e:
            log.warning("取 pubmed 全文失败 %s: %s", pmid, e)
            return ""

    # ---------- elink 关联 SRA (测序数据) ----------
    def elink_sra(self, gds_uids: list[str]) -> dict:
        """从 GEO gds uid 关联到 SRA (测序原始数据), 返回 {gds_uid: [sra_ids]}."""
        return self.elink_target(gds_uids, "sra")

    def elink_target(self, ids: list[str], target_db: str) -> dict:
        """从 ids 关联到 target_db, 返回 {src_id: [target_ids]}."""
        if not ids:
            return {}
        data = self._get(f"{EUTILS}/elink.fcgi",
                         self._params({"dbfrom": "gds", "db": target_db, "id": ",".join(ids)}))
        out = {}
        for ls in data.get("linksets", []) or []:
            src = str(ls.get("ids", [""])[0])
            tgt = []
            for ldb in ls.get("linksetdbs", []) or []:
                tgt.extend([str(x) for x in ldb.get("links", [])])
            out[src] = tgt
        return out

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


# ---------- 数据可用性链接提取 (从文献全文) ----------
# 已知数据库 accession/URL 模式
_DATA_PATTERNS = [
    ("GEO", re.compile(r"(GSE\d{4,8})")),
    ("SRA", re.compile(r"((?:SRP|SRR|SRX|PRJNA|PRJDB|PRJEB)\d{4,})")),
    ("ArrayExpress", re.compile(r"(E-\w+-\d{3,})")),
    ("Zenodo", re.compile(r"(https?://zenodo\.org/record/\d+)")),
    ("figshare", re.compile(r"(https?://\w*figshare\.com/\S+?)(?:[\s.)]|$)")),
    ("Dryad", re.compile(r"(https?://datadryad\.org/\S+?)(?:[\s.)]|$)")),
    ("GenomeWeb/BGI-STOmics", re.compile(r"(https?://\w*(?:stomics|cngb|genomics\.cn)\S+?)(?:[\s.)]|$)")),
    ("Single_Cell_Portal", re.compile(r"(https?://singlecell\.broadinstitute\.org/\S+?)(?:[\s.)]|$)")),
    ("HCA", re.compile(r"(https?://data\.humancellatlas\.org/\S+?)(?:[\s.)]|$)") if False else None),
    ("Generic_URL", re.compile(r"(https?://\S+?(?:download|data|suppl|repository|accession)\S*?)(?:[\s.)]|$)")),
]

_AVAIL_KEYWORDS = ("data availability", "data access", "data deposition", "data deposit",
                   "accession number", "accession code", "deposited in", "available at",
                   "data have been deposited", "publicly available")


def extract_data_links(text: str) -> list[dict]:
    """从文献全文/摘要提取数据可用性声明中的数据链接与 accession.

    返回 [{db, value, source}]. 策略: 优先扫 data availability 段; 无命中则全文扫描.
    """
    if not text:
        return []
    low = text.lower()
    # 1. 定位 data availability 段落 (关键词附近 ±800 字符)
    snippets = []
    for kw in _AVAIL_KEYWORDS:
        idx = low.find(kw)
        if idx != -1:
            snippets.append(text[max(0, idx - 200): idx + 800])
    region = " ".join(snippets) if snippets else ""
    # 2. 去重提取
    seen, out = set(), []
    pats = [(d, p) for d, p in _DATA_PATTERNS if p is not None]
    for db, pat in pats:
        for m in (pat.findall(region) if region and pat else []):
            val = m.strip().rstrip(",.;:)")
            if val and val not in seen and len(val) < 200:
                seen.add(val)
                out.append({"db": db, "value": val, "source": "availability_section"})
    # 3. 若段落无命中, 全文扫描 (摘要里常直接写 "data at GSE12345")
    if not out:
        for db, pat in pats:
            for m in pat.findall(text) if pat else []:
                val = m.strip().rstrip(",.;:)")
                if val and val not in seen and len(val) < 200:
                    seen.add(val)
                    out.append({"db": db, "value": val, "source": "fulltext_scan"})
    return out
