"""注释统一数据契约 (schemas).

定义两个核心产物的结构:
- CorpusRow:        文献级语料 (corpus.csv) 每行 — 从一篇文献抓取的一个 raw_label
- MappingEntry / MappingTable: 版本化映射表 (mapping_table.v{N}.json) — raw_label -> standard

设计要点:
- MappingEntry 的业务主键是 (raw_label, species, tissue) 三元组, 避免跨组织同词异义
  (如 "parenchyma" / "progenitor" 在叶/根/茎含义不同).
- provenance 必填: 每条映射带来源文献 + 原文证据 + 方法 + 状态, 这是"可统一/可信"的核心,
  也是比赛"真实可验证"的卖点.
- status 区分 confirmed(人工确认) / auto(LLM 自动回写) / review(待人工) / rejected(弃用).
- 时间戳 (harvested_at / mapped_at / created_at) 由调用方传入, 本模块不取 now
  (便于测试与复现; 工作流脚本统一打戳).
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path

# ---- 枚举 (字符串常量元组, 避免引入 enum, 校验时用 in) ----
SPECIES = ("Arabidopsis", "rice", "maize", "soybean", "tomato", "other")
TISSUES = ("leaf", "root", "stem", "flower", "seed", "meristem", "other")
SOURCE_TYPES = ("methods_table", "results_text", "figure_legend", "supplementary", "abstract")
CONFIDENCE_LEVELS = ("high", "mid", "low")   # high=文献显式标注, mid=推测, low=模糊
MAP_METHODS = ("synonym_exact", "ontology_normalize", "llm_proposal", "manual", "embedding_topk")
MAP_STATUS = ("confirmed", "auto", "review", "rejected")


@dataclass
class CorpusRow:
    """corpus.csv 的一行: 从某篇文献抓取的一个原始细胞类型注释."""
    paper_id: str            # Zotero itemID
    paper_title: str         # 文献简称
    doi: str = ""
    pmid: str = ""
    species: str = ""        # Arabidopsis / rice / ...
    tissue: str = ""         # leaf / root / stem / ...
    raw_label: str = ""      # 文献里的原始注释名 (如 "Mesophyll cell", "MSC")
    raw_label_zh: str = ""   # 中文名 (若有)
    marker_genes: str = ""   # 该类型 marker, 逗号分隔
    n_cells: str = ""        # 该类型细胞数 (文献给出则填; 字符串便于缺省)
    source_type: str = ""    # methods_table / results_text / figure_legend / supplementary / abstract
    evidence: str = ""       # 直接引用原文片段 (<=200字, 可溯源)
    confidence: str = "mid"  # high / mid / low
    harvested_at: str = ""   # 采集时间 (ISO, 由调用方传入)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.raw_label:
            errs.append("raw_label 不能为空")
        if not self.paper_id:
            errs.append("paper_id 不能为空")
        if self.confidence and self.confidence not in CONFIDENCE_LEVELS:
            errs.append(f"confidence 需在 {CONFIDENCE_LEVELS}")
        if self.source_type and self.source_type not in SOURCE_TYPES:
            errs.append(f"source_type 需在 {SOURCE_TYPES}")
        return errs

    @classmethod
    def csv_header(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_csv_row(self) -> list[str]:
        return [str(getattr(self, f.name) or "") for f in fields(self)]


@dataclass
class MappingEntry:
    """mapping_table 的一条: raw_label -> standard 映射, 带 provenance.

    业务主键 key = (raw_label, species, tissue); 同一 raw_label 在不同组织可映射到不同 standard.
    """
    raw_label: str
    species: str
    tissue: str
    standard: str                       # 粗粒度标准术语 (跨物种对齐, SAMap用); 确无对应填 "UNMAPPED"
    subtype: str = ""                   # 细粒度亚群 (保留分群信息, SATURN用); 如 "cortex-initial" / "A" / "mother-cell"
    standard_ontology: str = ""         # plant_leaf / plant_root / plant_stem
    method: str = "llm_proposal"        # synonym_exact / ontology_normalize / llm_proposal / manual / embedding_topk
    confidence: float = 0.0             # 0.0-1.0
    status: str = "review"              # confirmed / auto / review / rejected
    paper_ids: list[str] = field(default_factory=list)   # 来源 Zotero itemID 列表
    evidence: str = ""                  # 原文证据片段
    reviewer: str = ""
    mapped_at: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.raw_label, self.species, self.tissue)

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.raw_label:
            errs.append("raw_label 不能为空")
        if self.method not in MAP_METHODS:
            errs.append(f"method 需在 {MAP_METHODS}")
        if self.status not in MAP_STATUS:
            errs.append(f"status 需在 {MAP_STATUS}")
        if not (0.0 <= self.confidence <= 1.0):
            errs.append(f"confidence 需在 [0,1], 当前 {self.confidence}")
        if self.standard == "UNMAPPED" and self.status == "confirmed":
            errs.append("UNMAPPED 不应标记为 confirmed")
        return errs


@dataclass
class MappingTable:
    """版本化映射表 (mapping_table.v{N}.json)."""
    version: str
    ontologies: list[str] = field(default_factory=list)
    entries: list[MappingEntry] = field(default_factory=list)
    created_at: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "ontologies": self.ontologies,
            "note": self.note,
            "entries": [asdict(e) for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MappingTable":
        entries = [MappingEntry(**e) for e in d.get("entries", [])]
        return cls(
            version=d.get("version", ""),
            created_at=d.get("created_at", ""),
            ontologies=d.get("ontologies", []),
            note=d.get("note", ""),
            entries=entries,
        )

    def save(self, path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "MappingTable":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def index(self) -> dict[tuple, MappingEntry]:
        """构建 (raw_label, species, tissue) -> entry 索引, 供 map_to_standard 快速查表.

        跳过 rejected 条目. 多条同 key (不应发生) 取最后一条.
        """
        return {e.key: e for e in self.entries if e.status != "rejected"}

    def validate(self) -> list[str]:
        errs: list[str] = []
        seen: set = set()
        for e in self.entries:
            for m in e.validate():
                errs.append(f"[{e.raw_label}/{e.species}/{e.tissue}] {m}")
            if e.key in seen:
                errs.append(f"重复 key: {e.key}")
            seen.add(e.key)
        return errs
