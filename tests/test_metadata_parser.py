"""能力3 验证: MetadataParser 对已有水稻/拟南芥 h5ad 解析 obs + 映射标准注释.

用 h5seurat/{rice,at}_5k.h5ad (原始注释: tissue_cluster_names / integrated_annotation)
预期: 识别细胞类型列 + 映射到 PLANT_LEAF_ONTOLOGY, 复现跨物种整合统一命名.
"""
import sys, os, json
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.metadata_parser import MetadataParser
from omicagent.ontology import load_ontology

def test_one(name, path):
    print(f"\n===== 能力3: {name} ({path}) =====")
    mp = MetadataParser()
    a = mp.load(path)
    print(f"载入: {a.shape}, obs 列: {list(a.obs.columns)[:12]}...")
    ins = mp.inspect_columns(a)
    print(f"列识别: celltype={ins.celltype_col} sample={ins.sample_col} batch={ins.batch_col}")
    if not ins.celltype_col:
        print(f"  (未识别到细胞类型列, 可用列: {list(a.obs.columns)})")
        return None
    mr = mp.map_to_standard(a, ins.celltype_col, load_ontology("plant_leaf"))
    print(f"映射 (覆盖 {mr.coverage:.1%}):")
    for orig, std in mr.mapping.items():
        conf = mr.confidence.get(orig, 0)
        print(f"  {orig:30s} -> {std:20s} (conf={conf:.2f})")
    if mr.unmapped:
        print(f"  未映射: {mr.unmapped}")
    a = mp.apply_mapping(a, ins.celltype_col, mr)
    print(f"标准列分布: {dict(a.obs['celltype_standard'].value_counts())}")
    # 存映射表
    out = os.path.expanduser(f"~/bioinfo/agent_framework/results/mapping_{name}.json")
    with open(out, "w") as f:
        json.dump(mr.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"映射表已存: {out}")
    return mr

def main():
    base = "/home/miaoxiyu/bioinfo/data/h5seurat"
    r1 = test_one("rice", f"{base}/rice_5k.h5ad")
    r2 = test_one("at", f"{base}/at_5k.h5ad")
    # 验证跨物种共享类型命名一致
    if r1 and r2:
        s1 = set(r1.mapping.values())
        s2 = set(r2.mapping.values())
        shared = s1 & s2 - {"UNMAPPED"}
        print(f"\n===== 跨物种共享标准类型 ({len(shared)}) =====")
        print(sorted(shared))
        assert "Mesophyll" in shared, "Mesophyll 应在两物种均映射到"
        print("\n能力3 验证通过 ✅")

if __name__ == "__main__":
    main()
