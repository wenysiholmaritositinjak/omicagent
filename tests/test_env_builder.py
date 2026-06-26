"""能力2 验证: EnvBuilder 分析元信息 + 复用已有 conda env + 验证包可导入."""
import sys, os, json
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.env_builder import EnvBuilder

def main():
    eb = EnvBuilder()
    print("===== 能力2: 环境搭建 =====")
    # (A) 智能分析: LLM 根据元信息判断工具 (展示能力)
    meta = {"species": "Oryza sativa", "modality": "snRNA-seq", "format": "h5ad",
            "n_cells": 5000, "analysis_goal": "QC + clustering with scanpy"}
    spec_analyze = eb.analyze(meta, analysis_goal=meta["analysis_goal"])
    print(f"(A) 智能分析: tools={spec_analyze.analysis_tools} env={spec_analyze.env_name} exists={spec_analyze.exists}")

    # (B) 确定性: 直接用 scanpy 工具 -> scagent env (包齐全, 验证通过)
    spec = eb.ensure_env_for_tool("scanpy")
    print(f"(B) ensure_env scanpy: env={spec.env_name} exists={spec.exists} verify={spec.verify_cmds}")
    result = eb.build(spec, reuse_existing=True)
    print(f"\nbuild 结果: success={result.success} missing={result.missing}")
    print("--- 验证日志(核心) ---")
    for line in result.verify_log.splitlines():
        if "[core]" in line or line.startswith("["):
            print(line)
    out = os.path.expanduser("~/bioinfo/agent_framework/results/env_result.json")
    with open(out, "w") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"\n环境结果已存: {out}")
    assert spec.exists, "应复用已有 conda env (scagent)"
    assert result.success, "scanpy 核心验证应通过"
    print("\n能力2 验证通过 ✅ (智能分析 + 复用 scagent + 核心包验证)")

if __name__ == "__main__":
    main()
