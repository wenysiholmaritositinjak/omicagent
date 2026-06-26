"""标准细胞类型本体 (Ontology).

为能力3 (语义理解组学元数据) 提供统一标准注释体系.
默认 PLANT_LEAF 来自跨物种整合 (水稻+拟南芥叶片) 已验证的统一命名,
可按组织/物种扩展.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class CellOntology:
    name: str
    terms: list[str] = field(default_factory=list)
    # 同义词 -> 标准术语 (小写键), 供 LLM 映射参考与快速归一
    synonyms: dict[str, str] = field(default_factory=dict)

    def normalize(self, label: str) -> str | None:
        """把一个标签归一到标准术语, 命中同义词则返回标准名, 否则 None."""
        if label is None:
            return None
        k = str(label).strip().lower()
        if k in self.synonyms:
            return self.synonyms[k]
        # 直接匹配标准术语 (大小写不敏感)
        for t in self.terms:
            if t.lower() == k:
                return t
        return None

    def to_prompt(self) -> str:
        """生成供 LLM 映射参考的本体描述文本."""
        lines = [f"标准细胞类型体系 [{self.name}]:"]
        for t in self.terms:
            syns = [s for s, v in self.synonyms.items() if v == t]
            if syns:
                lines.append(f"  - {t} (同义: {', '.join(syns)})")
            else:
                lines.append(f"  - {t}")
        return "\n".join(lines)


# ---- 植物叶片标准体系 (跨物种整合已验证) ----
PLANT_LEAF_ONTOLOGY = CellOntology(
    name="plant_leaf",
    terms=[
        "Mesophyll", "Guard_cell", "Epidermis", "Vascular",
        "Bundle_sheath", "Companion_cell", "Xylem", "Phloem_parenchyma",
        "Fiber", "Parenchyma_cell", "Dividing_cell",
    ],
    synonyms={
        # Mesophyll
        "mesophyll": "Mesophyll", "palisade": "Mesophyll", "spongy": "Mesophyll",
        # Guard cell
        "guard cell": "Guard_cell", "guard_cell": "Guard_cell", "stomatal": "Guard_cell",
        "stomata": "Guard_cell", "气孔细胞": "Guard_cell", "保卫细胞": "Guard_cell",
        # Epidermis
        "epidermis": "Epidermis", "epidermal": "Epidermis", "表皮": "Epidermis",
        # Vascular
        "vascular": "Vascular", "vascular cylinder": "Vascular",
        "vascular_cylinder": "Vascular", "vascular cell": "Vascular",
        "维管": "Vascular", "维管束": "Vascular",
        # Bundle sheath
        "bundle sheath": "Bundle_sheath", "bundle_sheath": "Bundle_sheath",
        "束鞘": "Bundle_sheath",
        # Companion cell
        "companion cell": "Companion_cell", "companion_cell": "Companion_cell",
        "伴胞": "Companion_cell",
        # Xylem
        "xylem": "Xylem", "木质部": "Xylem",
        # Phloem parenchyma
        "phloem parenchyma": "Phloem_parenchyma",
        "phloem_parenchyma": "Phloem_parenchyma",
        "phloem_parenchyma/bundle_sheath": "Phloem_parenchyma",
        "韧皮薄壁": "Phloem_parenchyma",
        # Fiber
        "fiber": "Fiber", "fibre": "Fiber", "纤维": "Fiber",
        # Parenchyma
        "parenchyma cell": "Parenchyma_cell", "parenchyma_cell": "Parenchyma_cell",
        "parenchyma": "Parenchyma_cell", "薄壁细胞": "Parenchyma_cell",
        # Dividing
        "dividing cell": "Dividing_cell", "dividing_cell": "Dividing_cell",
        "divding cell": "Dividing_cell", "proliferating": "Dividing_cell",
        "分裂细胞": "Dividing_cell",
    },
)


_ONTOLOGIES = {
    "plant_leaf": PLANT_LEAF_ONTOLOGY,
    "default": PLANT_LEAF_ONTOLOGY,
}


def load_ontology(name: str = "plant_leaf") -> CellOntology:
    """按名称加载标准本体; 未知名称回退到 plant_leaf."""
    return _ONTOLOGIES.get(name, PLANT_LEAF_ONTOLOGY)


def register_ontology(name: str, ont: CellOntology) -> None:
    """注册自定义本体, 供扩展 (植物根/动物等)."""
    _ONTOLOGIES[name] = ont
