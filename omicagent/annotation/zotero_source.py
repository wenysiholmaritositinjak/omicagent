"""Zotero 文献源 (只读 zotero.sqlite).

不依赖 Zotero 运行, 直接只读连接 zotero.sqlite + storage/ 目录,
按 collection / tag 筛选植物单细胞文献, 输出文献清单 (含 PDF 物理路径).

输出 zotero_index.json: [{item_id, key, title, doi, pmid, year, publication,
   abstract, pdf_path, collections, tags, species_guess, tissue_guess}]

接入点: annotation_harvester 据此清单逐篇取 PDF 抽注释.
"""
from __future__ import annotations
import json, logging, re, sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger("omicagent.annotation.zotero")

DEFAULT_ZOTERO_DIR = "/mnt/c/Users/zhangziyi5/Zotero"

# 命中即视为植物单细胞相关文献的 collection 名 (精确匹配)
PLANT_SC_COLLECTIONS = {
    "水稻茎节", "tissue", "单细胞技术", "植物时空组学重要文献",
    "植物空间组学综述", "空间转录组", "JXB_review", "参考文献",
}
# tag 小写包含命中
PLANT_SC_TAG_KEYWORDS = (
    "single-cell", "single cell", "scrna", "single-nucleus", "snrna",
    "plant", "arabidopsis", "rice", "maize", "soybean", "root", "leaf", "stem",
    "cell atlas", "cell type", "meristem", "spatial transcriptom",
)
# 物种/组织猜测关键词 (用于 species_guess / tissue_guess, 非穷尽, 仅辅助)
SPECIES_KEYWORDS = {
    "arabidopsis": "Arabidopsis", "拟南芥": "Arabidopsis",
    "rice": "rice", "oryza": "rice", "水稻": "rice",
    "maize": "maize", "zea mays": "maize", "玉米": "maize",
    "soybean": "soybean", "glycine": "soybean", "大豆": "soybean",
    "tomato": "tomato", "solanum": "tomato",
}
TISSUE_KEYWORDS = {
    "leaf": "leaf", "leaves": "leaf", "叶片": "leaf",
    "root": "root", "根": "root",
    "stem": "stem", "node": "stem", "茎": "stem", "节间": "stem",
    "flower": "flower", "花": "flower",
    "seed": "seed", "embryo": "seed", "种子": "seed", "胚": "seed",
    "meristem": "meristem", "分生": "meristem",
}


@dataclass
class PaperRecord:
    item_id: str
    key: str
    title: str
    doi: str = ""
    year: str = ""
    publication: str = ""
    abstract: str = ""
    extra: str = ""
    pdf_path: str = ""
    collections: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    species_guess: str = ""
    tissue_guess: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def pmid(self) -> str:
        """从 extra 字段抽 PMID (Zotero 常存 'PMID: 12345')."""
        m = re.search(r"PMID:?\s*(\d+)", self.extra or "")
        return m.group(1) if m else ""


class ZoteroSource:
    def __init__(self, zotero_dir: str = DEFAULT_ZOTERO_DIR):
        self.zotero_dir = Path(zotero_dir)
        self.db_path = self.zotero_dir / "zotero.sqlite"
        self.storage_dir = self.zotero_dir / "storage"
        if not self.db_path.exists():
            raise FileNotFoundError(f"zotero.sqlite 不存在: {self.db_path}")

    def _connect(self) -> sqlite3.Connection:
        # 只读 URI 连接, 不锁库 (Zotero 运行时也能并发读)
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def _field_value(self, con, item_id, field_name) -> str:
        row = con.execute(
            "select v.value from itemData d join fields f on d.fieldID=f.fieldID "
            "join itemDataValues v on d.valueID=v.valueID "
            "where d.itemID=? and f.fieldName=?",
            (item_id, field_name)
        ).fetchone()
        return row[0] if row else ""

    def _pdf_path(self, con, item_id) -> str:
        """取条目关联的第一个 PDF 附件物理路径."""
        rows = con.execute(
            "select a.path, i2.key from itemAttachments a "
            "join items i2 on a.itemID=i2.itemID "
            "where a.parentItemID=? and a.contentType='application/pdf'",
            (item_id,)
        ).fetchall()
        for path, attach_key in rows:
            # path 形如 "storage:filename.pdf" (旧) 或 "storage:KEY/filename.pdf"
            # 附件物理目录用附件自己的 key: storage/<attach_key>/<filename>
            if not path or not path.startswith("storage:"):
                continue
            rel = path[len("storage:"):]
            fname = rel.split("/", 1)[1] if "/" in rel else rel
            p = self.storage_dir / attach_key / fname
            if p.exists():
                return str(p)
        return ""

    def _collections(self, con, item_id) -> list[str]:
        rows = con.execute(
            "select c.collectionName from collectionItems ci "
            "join collections c on ci.collectionID=c.collectionID "
            "where ci.itemID=?", (item_id,)
        ).fetchall()
        return [r[0] for r in rows]

    def _tags(self, con, item_id) -> list[str]:
        rows = con.execute(
            "select t.name from itemTags it join tags t on it.tagID=t.tagID "
            "where it.itemID=?", (item_id,)
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _is_plant_sc(collections: list[str], tags: list[str]) -> bool:
        for c in collections:
            if c in PLANT_SC_COLLECTIONS:
                return True
        low_tags = " ".join(tags).lower()
        return any(kw in low_tags for kw in PLANT_SC_TAG_KEYWORDS)

    def list_plant_sc_papers(self) -> list[PaperRecord]:
        """列出植物单细胞相关文献 (journalArticle/preprint), 按 collection/tag 筛选."""
        con = self._connect()
        try:
            rows = con.execute(
                "select i.itemID, i.key from items i "
                "join itemTypes t on i.itemTypeID=t.itemTypeID "
                "where t.typeName in ('journalArticle', 'preprint') "
                "order by i.dateAdded desc"
            ).fetchall()
            out: list[PaperRecord] = []
            for item_id, key in rows:
                cols = self._collections(con, item_id)
                tags = self._tags(con, item_id)
                if not self._is_plant_sc(cols, tags):
                    continue
                title = self._field_value(con, item_id, "title")
                abstract = self._field_value(con, item_id, "abstractNote")
                haystack = " ".join([title, abstract, " ".join(tags), " ".join(cols)]).lower()
                out.append(PaperRecord(
                    item_id=str(item_id), key=key, title=title,
                    doi=self._field_value(con, item_id, "DOI"),
                    year=_extract_year(self._field_value(con, item_id, "date")),
                    publication=self._field_value(con, item_id, "publicationTitle"),
                    abstract=abstract,
                    extra=self._field_value(con, item_id, "extra"),
                    pdf_path=self._pdf_path(con, item_id),
                    collections=cols, tags=tags,
                    species_guess=_guess(haystack, SPECIES_KEYWORDS),
                    tissue_guess=_guess(haystack, TISSUE_KEYWORDS),
                ))
            return out
        finally:
            con.close()

    def save_index(self, papers: list[PaperRecord], out_path: str) -> None:
        data = {
            "zotero_dir": str(self.zotero_dir),
            "n_papers": len(papers),
            "papers": [p.to_dict() for p in papers],
        }
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("zotero_index 已存: %s (%d 篇)", out_path, len(papers))


def _extract_year(date_str: str) -> str:
    if not date_str:
        return ""
    m = re.search(r"(19|20)\d{2}", date_str)
    return m.group(0) if m else ""


def _guess(haystack_lower: str, kw_map: dict) -> str:
    """从文本(已小写)按关键词表猜物种/组织, 命中多个用逗号连."""
    hits: list[str] = []
    for kw, std in kw_map.items():
        if kw in haystack_lower and std not in hits:
            hits.append(std)
    return ",".join(hits)
