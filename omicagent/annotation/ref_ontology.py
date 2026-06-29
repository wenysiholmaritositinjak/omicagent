"""scPlantDB 参考本体 (ref).

从 data/annotation/scplantdb_ref.json 加载 scPlantDB 的 80 个跨物种多组织细胞类型名,
建为 CellOntology("plant_sc") 并 register_ontology, 作为注释统一的 ref 命名体系.

设计:
- scPlantDB 命名粒度较细 (如 'Palisade mesophyll' 而非 'Palisade', 'Vascular bundle' 而非 'Vascular').
- synonyms 只含安全变体 (原样/小写/下划线/连字符/去 ' cell' 后缀), 不做推测性映射.
- normalize 未命中者走 LLM 兜底 (P1 annotation_harvester), 不在此硬补 synonyms 以免错映射.
- 不替换默认 plant_leaf 本体; 需用时显式 load_scplantdb_ontology() 传入 map_to_standard.
"""
from __future__ import annotations
import json
from pathlib import Path

from .. import config
from ..ontology import CellOntology, register_ontology

_REF_PATH = config.PROJECT_ROOT / "data" / "annotation" / "scplantdb_ref.json"


def _build_synonyms(terms: list[str]) -> dict[str, str]:
    """安全变体: 原样/小写/下划线/连字符/去 ' cell' 后缀. 不做推测性映射."""
    syn: dict[str, str] = {}
    for t in terms:
        keys = {t, t.lower(), t.lower().replace(" ", "_"),
                t.lower().replace(" ", "-"),
                t.lower().replace(" cell", "")}
        for k in keys:
            syn[k] = t
    return syn


def load_scplantdb_ontology(path=None) -> CellOntology:
    """加载 scPlantDB ref 为 CellOntology 并 register 为 'plant_sc'."""
    p = Path(path) if path else _REF_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    terms = [r["name"] for r in data["celltypes"]]
    ont = CellOntology(name="plant_sc", terms=terms, synonyms=_build_synonyms(terms))
    register_ontology("plant_sc", ont)
    return ont
