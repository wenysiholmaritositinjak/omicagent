"""unify_h5ad.py — 真实 h5ad 文件的细胞注释统一 (需 scanpy).

用法:
  python skills/plant_annotation/unify_h5ad.py <input.h5ad> [--species rice] [--tissue leaf]

输出: <input>_unified.h5ad (obs 新增 celltype_standard + celltype_subtype 列)

需: pip install scanpy anndata (oa-venv 默认未装, 按需安装)
"""
import sys, os, argparse
REPO = os.path.expanduser("~/omicagent")
sys.path.insert(0, REPO)
from omicagent.metadata_parser import MetadataParser
from omicagent.annotation.ref_ontology import load_scplantdb_ontology
from omicagent.annotation.mapping_store import MappingStore
from omicagent.llm_client import LLMClient
from omicagent import config


def main():
    ap = argparse.ArgumentParser(description="统一 h5ad 细胞注释 → celltype_standard + celltype_subtype")
    ap.add_argument("input", help="输入 h5ad 路径")
    ap.add_argument("--species", default="", help="物种 (rice/Arabidopsis/...), 辅助查表消歧")
    ap.add_argument("--tissue", default="", help="组织 (leaf/root/stem), 辅助查表消歧")
    args = ap.parse_args()

    import scanpy as sc  # 需 scanpy
    mp = MetadataParser(llm=LLMClient())
    a = mp.load(args.input)
    ins = mp.inspect_columns(a)
    if not ins.celltype_col:
        print(f"未识别到细胞类型列, 可用列: {list(a.obs.columns)}")
        sys.exit(1)
    print(f"识别细胞类型列: {ins.celltype_col} ({a.n_obs} 细胞)")

    ont = load_scplantdb_ontology()
    store_path = config.PROJECT_ROOT / "data" / "annotation" / "mapping_table.v4.json"
    store = MappingStore(str(store_path)) if store_path.exists() else None
    mr = mp.map_to_standard(a, ins.celltype_col, ont,
                            mapping_store=store, species=args.species, tissue=args.tissue)
    a = mp.apply_mapping(a, ins.celltype_col, mr)

    # 写 celltype_subtype (保特异群)
    if store:
        sub_map = {}
        for lab in a.obs[ins.celltype_col].astype(str).unique():
            e = store.lookup(lab, args.species, args.tissue)
            if e and e.subtype:
                sub_map[lab] = e.subtype
            elif mr.mapping.get(lab, "UNMAPPED") == "UNMAPPED":
                sub_map[lab] = "specific:" + str(lab).lower().replace(" ", "-")
            else:
                sub_map[lab] = ""
        a.obs["celltype_subtype"] = a.obs[ins.celltype_col].astype(str).map(sub_map).fillna("")

    out = args.input.replace(".h5ad", "_unified.h5ad")
    a.write_h5ad(out)
    print(f"\n覆盖率: {mr.coverage:.0%}  标准类型: {len(set(mr.mapping.values())-{'UNMAPPED'})}")
    print(f"特异群(UNMAPPED): {len(mr.unmapped)}")
    print(f"已写回: {out}")
    print(f"  新增列: celltype_standard (SAMap用), celltype_subtype (SATURN用/保特异群)")
    print(f"\n下一步: 拿 {out} 接 run_cross_species (celltype_standard 列对齐)")


if __name__ == "__main__":
    main()
