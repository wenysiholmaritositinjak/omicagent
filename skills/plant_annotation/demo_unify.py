"""plant_annotation skill 端到端演示.

模拟"统一两个叶片数据集细胞注释"的完整流程:
  数据集1: 水稻叶片 (paper 1931, 29 类注释)
  数据集2: 拟南芥叶片 (paper 28, 11 类注释)
  → unify_annotation 表优先统一 (standard + subtype)
  → 保留特异群 (UNMAPPED + subtype)
  → 写回 celltype_standard + celltype_subtype 列
  → 跨物种对齐报告 (SAMap 可直接用 standard 列)

直接跑: python skills/plant_annotation/demo_unify.py
(不依赖 scanpy/h5ad, 用 corpus 真实文献注释模拟; 真实 h5ad 用 unify_annotation 工具)
"""
import sys, os, csv
import pandas as pd

# 路径
HOME = os.path.expanduser("~")
REPO = os.path.join(HOME, "omicagent")
sys.path.insert(0, REPO)
DATA = os.path.join(REPO, "data", "annotation")

from omicagent.metadata_parser import MetadataParser
from omicagent.annotation.ref_ontology import load_scplantdb_ontology
from omicagent.annotation.mapping_store import MappingStore


class FakeAdata:
    """模拟 AnnData (仅 obs, 够演示 map_to_standard + 写回)."""
    def __init__(self, obs):
        self.obs = obs
        self.n_obs = len(obs)
        self.n_vars = 10
    def write_h5ad(self, p):
        # 演示用: 写 obs 为 csv (真实场景用 scanpy 写 h5ad)
        self.obs.to_csv(p.replace(".h5ad", "_obs.csv"), index=False)


class MockLLM:
    """不调 LLM (表优先全命中), inspect_columns 兜底返回 dict."""
    def complete_json(self, *a, **k):
        return {}


def load_dataset(name, paper_id, species, tissue):
    """从 corpus 取真实文献注释, 模拟一个数据集."""
    rows = [r for r in csv.DictReader(open(os.path.join(DATA, "corpus.csv"), encoding="utf-8"))
            if r["paper_id"] == paper_id]
    labels = [r["raw_label"] for r in rows]
    obs = pd.DataFrame({"celltype": labels})
    print(f"\n{'='*70}")
    print(f"数据集: {name} ({species} {tissue})")
    print(f"{'='*70}")
    print(f"细胞数(模拟): {len(labels)}  原始注释类型: {len(set(labels))}")
    print(f"原始注释: {labels}")
    return FakeAdata(obs), labels


def unify_dataset(name, adata, species, tissue, mp, ont, store):
    """对单个数据集跑 unify_annotation (表优先, 写回 standard+subtype)."""
    ins = mp.inspect_columns(adata)
    mr = mp.map_to_standard(adata, ins.celltype_col, ont,
                            mapping_store=store, species=species, tissue=tissue)
    adata = mp.apply_mapping(adata, ins.celltype_col, mr)
    # 写 celltype_subtype (从 store 查, 保留特异群)
    sub_map = {}
    for lab in adata.obs[ins.celltype_col].astype(str).unique():
        e = store.lookup(lab, species, tissue)
        if e and e.subtype:
            sub_map[lab] = e.subtype
        elif mr.mapping.get(lab) == "UNMAPPED":
            sub_map[lab] = "specific:" + str(lab).lower().replace(" ", "-")  # 特异群标记
        else:
            sub_map[lab] = ""
    adata.obs["celltype_subtype"] = adata.obs[ins.celltype_col].astype(str).map(sub_map).fillna("")
    return adata, mr, ins.celltype_col


def report(name, adata, mr, labels, col):
    """打印单数据集统一结果."""
    print(f"\n--- {name} 统一结果 ---")
    print(f"覆盖率: {mr.coverage:.0%}  统一后标准类型: {len(set(mr.mapping.values())-{'UNMAPPED'})}")
    print(f"{'原始注释':32s} → {'celltype_standard':22s} {'celltype_subtype':22s}")
    print("-" * 80)
    for lab in labels:
        std = mr.mapping.get(lab, "UNMAPPED")
        sub = adata.obs.loc[adata.obs[col] == lab, "celltype_subtype"].iloc[0] if (adata.obs[col] == lab).any() else ""
        mark = "  (特异群)" if std == "UNMAPPED" else ""
        print(f"{lab:32s} → {std:22s} {sub:22s}{mark}")


def main():
    print("=" * 70)
    print("plant_annotation skill 演示: 统一两个叶片数据集细胞注释")
    print("目标: 跨物种整合前置 — 统一命名 + 保留特异群 → 接 SAMap/SATURN")
    print("=" * 70)

    mp = MetadataParser(llm=MockLLM())
    ont = load_scplantdb_ontology()
    store = MappingStore(os.path.join(DATA, "mapping_table.v4.json"))
    print(f"\n已加载: plant_sc 本体 (80 标准名) + mapping_table.v4 ({len(store.table.entries)} 条, 13 物种)")

    # 1) 加载两个数据集 (真实文献注释)
    a1, labs1 = load_dataset("数据集1", "1931", "rice", "leaf")        # 水稻叶
    a2, labs2 = load_dataset("数据集2", "28", "Arabidopsis", "leaf")   # 拟南芥叶

    # 2) 分别统一
    a1, mr1, col1 = unify_dataset("数据集1", a1, "rice", "leaf", mp, ont, store)
    a2, mr2, col2 = unify_dataset("数据集2", a2, "Arabidopsis", "leaf", mp, ont, store)
    report("数据集1 (水稻叶)", a1, mr1, labs1, col1)
    report("数据集2 (拟南芥叶)", a2, mr2, labs2, col2)

    # 3) 写回 (模拟, 真实场景 unify_annotation 工具写 h5ad)
    out1 = os.path.join(REPO, "results", "rice_leaf_unified_obs.csv")
    out2 = os.path.join(REPO, "results", "at_leaf_unified_obs.csv")
    os.makedirs(os.path.dirname(out1), exist_ok=True)
    a1.obs.to_csv(out1, index=False)
    a2.obs.to_csv(out2, index=False)
    print(f"\n>> 已写回 (含 celltype_standard + celltype_subtype 列):")
    print(f"   数据集1: {out1}")
    print(f"   数据集2: {out2}")

    # 4) 跨物种对齐报告 (SAMap 用 standard 列)
    std1 = set(mr1.mapping.values()) - {"UNMAPPED"}
    std2 = set(mr2.mapping.values()) - {"UNMAPPED"}
    shared = std1 & std2
    only1 = std1 - std2
    only2 = std2 - std1
    print(f"\n{'='*70}")
    print("跨物种对齐报告 (SAMap 直接用 celltype_standard 列)")
    print(f"{'='*70}")
    print(f"数据集1 标准类型 ({len(std1)}): {sorted(std1)}")
    print(f"数据集2 标准类型 ({len(std2)}): {sorted(std2)}")
    print(f"\n>>> 共享类型 (跨物种可直接对齐, {len(shared)}): {sorted(shared)}")
    print(f">>> 仅水稻 ({len(only1)}): {sorted(only1)}")
    print(f">>> 仅拟南芥 ({len(only2)}): {sorted(only2)}")

    # 5) 特异群 (UNMAPPED, 保留原注释 + specific: 标记, SATURN 可用 subtype 列追踪)
    spec1 = [lab for lab in labs1 if mr1.mapping.get(lab, "UNMAPPED") == "UNMAPPED"]
    spec2 = [lab for lab in labs2 if mr2.mapping.get(lab, "UNMAPPED") == "UNMAPPED"]
    print(f"\n{'='*70}")
    print("特异群保留 (scPlantDB 盲区, 不强行映射, 保留原标签 + specific: 标记)")
    print(f"{'='*70}")
    print(f"数据集1 特异群 ({len(spec1)}): {spec1}")
    print(f"数据集2 特异群 ({len(spec2)}): {spec2}")

    print(f"\n{'='*70}")
    print("下一步: 拿 celltype_standard 列接 SAMap (跨物种对齐)")
    print("       拿 celltype_subtype 列接 SATURN (保留亚群/特异群)")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
