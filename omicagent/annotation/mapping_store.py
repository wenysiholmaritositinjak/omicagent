"""版本化 mapping 表存储 (MappingStore).

封装 MappingTable 的加载/查询/upsert/版本/持久化, 供 map_to_standard 表优先使用.

设计:
- lookup(raw_label, species, tissue): O(1) 查表 (三元 key), 命中返回 entry, 跳过 rejected.
- upsert(entry): 新增或更新同 key 条目. 同 key 已为 confirmed 时默认不覆盖 (保护人工确认结果).
- save(): 脏则写回 (带版本). 回写策略: LLM 兜底结果以 status='auto' 写入, 待人工 review.

接入点: MetadataParser.map_to_standard(mapping_store=...) 查表优先, LLM 兜底结果回写.
"""
from __future__ import annotations
import logging
from collections import Counter
from pathlib import Path
from typing import Optional

from .schemas import MappingTable, MappingEntry

log = logging.getLogger("omicagent.annotation.store")


class MappingStore:
    def __init__(self, path: str, ontology_names: Optional[list[str]] = None):
        self.path = Path(path)
        if self.path.exists():
            self.table = MappingTable.load(self.path)
        else:
            self.table = MappingTable(
                version="v1",
                ontologies=ontology_names or [],
                entries=[],
            )
        self._index = self.table.index()
        self._dirty = False

    def lookup(self, raw_label: str, species: str, tissue: str) -> Optional[MappingEntry]:
        """按 (raw_label, species, tissue) 三元 key 查表.

        精确三元未命中时, 逐级回退 (raw_label 唯一即可命中, 不强求 species/tissue):
        1) 同 raw_label + species, 任意 tissue (confirmed 优先)
        2) 同 raw_label, 任意 species/tissue (confirmed 优先, 否则最高置信)
        """
        e = self._index.get((raw_label, species, tissue))
        if e is not None:
            return e
        cands = [ent for ent in self.table.entries
                 if ent.raw_label == raw_label and ent.status != "rejected"]
        if not cands:
            return None
        # 优先 species 匹配
        if species:
            sp_cands = [c for c in cands if species in (c.species or "")]
            if sp_cands:
                cands = sp_cands
        # 优先 tissue 匹配
        if tissue:
            ti_cands = [c for c in cands if c.tissue == tissue]
            if ti_cands:
                cands = ti_cands
        # confirmed 优先, 否则最高置信
        confirmed = [c for c in cands if c.status == "confirmed"]
        if confirmed:
            return confirmed[0]
        return max(cands, key=lambda c: c.confidence)

    def upsert(self, entry: MappingEntry, overwrite_confirmed: bool = False) -> bool:
        """新增或更新. 同 key 已为 confirmed 时默认不覆盖. 返回是否实际写入."""
        existing = self._index.get(entry.key)
        if existing is not None:
            if existing.status == "confirmed" and not overwrite_confirmed:
                return False
            self.table.entries = [e for e in self.table.entries if e.key != entry.key]
        self.table.entries.append(entry)
        self._index[entry.key] = entry
        self._dirty = True
        return True

    def save(self) -> None:
        if not self._dirty:
            return
        errs = self.table.validate()
        if errs:
            log.warning("mapping 表校验有问题, 仍保存: %s", errs[:3])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.table.save(self.path)
        self._dirty = False
        log.info("mapping 表已保存: %s (%d 条)", self.path, len(self.table.entries))

    @property
    def n_entries(self) -> int:
        return len(self.table.entries)

    def stats(self) -> dict:
        return {
            "n_entries": len(self.table.entries),
            "by_status": dict(Counter(e.status for e in self.table.entries)),
            "by_method": dict(Counter(e.method for e in self.table.entries)),
        }
