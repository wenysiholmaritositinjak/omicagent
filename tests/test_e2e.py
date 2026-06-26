"""端到端验证: OmicAgent.search_and_analyze 串联三能力.

用本地拟南芥 h5ad (避免下载), 验证: 检索(GEO) -> 元数据解析(映射标准) -> 建环境 -> 生成脚本 -> 报告.
"""
import sys, os, json
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.pipeline import OmicAgent

def main():
    agent = OmicAgent()
    user_input = "拟南芥叶片单细胞数据，包含气孔细胞 (Arabidopsis leaf single cell, guard cell)"
    local = "/home/miaoxiyu/bioinfo/data/h5seurat/at_5k.h5ad"
    out = agent.search_and_analyze(user_input, local_data=local, render_report=True)
    print("\n===== 端到端结果 =====")
    print("goal:", out.get("user_input", ""))
    st = out.get("stages", {})
    print("\n[1 检索] 记录数:", st.get("search", {}).get("n_records"),
          "可用源:", st.get("search", {}).get("sources_ok"),
          "耗时:", st.get("search", {}).get("elapsed"), "s")
    for r in st.get("search", {}).get("records", [])[:3]:
        print(f"   {r['accession']} ({r['source_db']}) rel={r['relevance']:.2f} {r['title'][:50]}")
    md = st.get("metadata", {})
    print("\n[3 元数据] celltype_col:", md.get("inspection", {}).get("celltype_col"))
    if md.get("mapping"):
        print("   映射覆盖:", md["mapping"].get("coverage"))
        print("   示例:", dict(list(md["mapping"].get("mapping", {}).items())[:4]))
    env = st.get("env", {})
    print("\n[2 环境] env:", env.get("spec", {}).get("env_name"),
          "tools:", env.get("spec", {}).get("analysis_tools"),
          "success:", env.get("success"))
    an = st.get("analysis", {})
    print("\n[4 分析] success:", an.get("success"))
    print("\n报告:", out.get("report"))
    print("token 用量:", out.get("usage", {}))
    # 存完整结果
    with open(os.path.expanduser("~/bioinfo/agent_framework/results/e2e_result.json"), "w") as f:
        json.dump({k: v for k, v in out.items() if k != "report"}, f, ensure_ascii=False, indent=2, default=str)
    assert st.get("search", {}).get("n_records", 0) > 0, "检索应有结果"
    assert env.get("success"), "环境应搭建成功"
    print("\n端到端验证通过 ✅")

if __name__ == "__main__":
    main()
