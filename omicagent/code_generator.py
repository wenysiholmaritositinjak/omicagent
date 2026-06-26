"""代码生成器 (Code Generator).

根据任务规划器输出 + 数据集元信息, 调用简单模型 (deepseek-v4-pro) 生成
R(Seurat 生态) 或 Python(Scanpy 生态) 分析脚本. 覆盖文档表3-3 六大模块.
生成后可执行, 失败则带错误信息重新生成 (<=3 次).
"""
from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Optional

from . import config
from .llm_client import LLMClient
from .tool_dispatcher import ToolDispatcher, ShellResult

log = logging.getLogger("omicagent.codegen")

# 文档表3-3 标准分析模块说明 (供 LLM 生成脚本时参考)
MODULES = {
    "qc": {
        "desc": "质控与过滤: 计算线粒体/叶绿体比例, 过滤低质量细胞与低表达基因",
        "r": "Seurat::PercentageFeatureSet + subset",
        "python": "scanpy.pp.calculate_qc_metrics + filtering",
    },
    "normalize": {
        "desc": "归一化与高变基因选择",
        "r": "Seurat::NormalizeData + FindVariableFeatures",
        "python": "scanpy.pp.normalize_total + highly_variable_genes",
    },
    "cluster": {
        "desc": "降维与聚类: PCA -> 邻域图 -> 聚类 -> UMAP",
        "r": "Seurat::RunPCA + FindNeighbors + FindClusters + RunUMAP",
        "python": "scanpy.tl.pca + neighbors + leiden + umap",
    },
    "annotate": {
        "desc": "细胞类型注释: marker 基因 + LLM 辅助映射标准注释",
        "r": "SingleR + celldex (参考数据集)",
        "python": "scanpy.tl.rank_genes_groups + LLM 辅助",
    },
    "coexpression": {
        "desc": "共表达模块 / 基因调控网络",
        "r": "hdWGCNA",
        "python": "SCENIC + pyscenic",
    },
    "cross_species": {
        "desc": "跨物种整合: 水稻+拟南芥叶片单细胞数据整合, 输出整合 UMAP 与同源基因模块",
        "r": "SATURN + SAMap",
        "python": "pySATURN (主) 或 1:1同源基因(reciprocal best hit via BLAST) + Harmony (回退)",
    },
}

LANG_RUNNER = {"python": "python", "r": "Rscript"}


class CodeGenerator:
    def __init__(self, llm: Optional[LLMClient] = None, dispatcher: Optional[ToolDispatcher] = None):
        self.llm = llm or LLMClient()
        self.dispatcher = dispatcher or ToolDispatcher(self.llm)
        self.scripts_dir = config.RESULTS_DIR / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, module: str, lang: str = "python", params: Optional[dict] = None,
                 data_info: str = "", task_desc: str = "") -> Path:
        """生成某模块的分析脚本, 返回脚本文件路径."""
        if module not in MODULES:
            raise ValueError(f"未知模块: {module}, 可选: {list(MODULES)}")
        if lang not in LANG_RUNNER:
            raise ValueError(f"不支持语言: {lang}, 可选: {list(LANG_RUNNER)}")
        params = params or {}
        spec = MODULES[module]

        prompt = (
            f"为单细胞组学分析模块 [{module}] 生成一个完整可运行的 {lang.upper()} 脚本.\n"
            f"模块说明: {spec['desc']}\n"
            f"推荐工具 ({lang}): {spec[lang]}\n"
            f"任务描述: {task_desc or spec['desc']}\n"
            f"数据/参数信息: {data_info or params}\n\n"
            f"要求 (Ponytail 懒惰阶梯 — 写最少必要代码):\n"
            f"1. 先判断这步是否必要(YAGNI), 不必要跳过\n"
            f"2. 优先复用 scanpy/seurat 现成函数, 不重写; 标准库/已装依赖能做就不引新包\n"
            f"3. 能一行搞定就一行, 不铺张\n"
            f"4. 只写能工作的最少代码; 但验证/错误处理/路径安全绝不省\n"
            f"- 完整可独立运行, 顶部注释说明输入输出路径\n"
            f"- 路径用参数/变量, 便于替换; 默认输入 ~/bioinfo/data, 输出 ~/bioinfo/results\n"
            f"- 含关键步骤日志 print 与异常处理\n"
            f"- 只输出代码, 不要解释文字, 不要 markdown 围栏"
        )
        resp = self.llm.complete(prompt, task_type="simple", max_tokens=6144, temperature=0.2)
        code = _strip_fences(resp.content)
        suffix = ".py" if lang == "python" else ".R"
        path = self.scripts_dir / f"{module}_{lang}{suffix}"
        path.write_text(code, encoding="utf-8")
        log.info("生成脚本: %s (%d 行)", path, code.count(chr(10)) + 1)
        return path

    def generate_and_run(self, module: str, lang: str = "python", params: Optional[dict] = None,
                         data_info: str = "", task_desc: str = "", max_attempts: int = 3,
                         env: str = "") -> ShellResult:
        """生成并执行脚本, 失败则带错误重新生成 (<=max_attempts 次).

        env: 指定 conda 环境名 (如 scagent/seurat/samap), 通过 `conda run -n <env>` 执行;
             为空则直接用系统 python/Rscript.
        """
        last_err = ""
        for attempt in range(1, max_attempts + 1):
            extra = f"\n上次执行失败的错误信息, 请修复:\n{last_err}" if last_err else ""
            path = self.generate(module, lang, params, data_info + extra, task_desc)
            runner = LANG_RUNNER[lang]
            if env:
                # 通过 conda run 在指定环境执行
                bin_py = f"conda run -n {env} python"
                bin_r = f"conda run -n {env} Rscript"
                runner_bin = bin_py if lang == "python" else bin_r
                result = self.dispatcher.run_shell(f"{runner_bin} {path}", timeout=3600)
            else:
                result = self.dispatcher.run_script_file(path, runner=runner, timeout=3600)
            if result.success:
                log.info("模块 %s 执行成功 (第 %d 次, env=%s)", module, attempt, env or "system")
                return result
            last_err = result.stderr[-1500:] or result.stdout[-1500:]
            log.warning("模块 %s 第 %d 次失败, 重试...", module, attempt)
        log.error("模块 %s %d 次重试均失败", module, max_attempts)
        return result


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip() + "\n"
