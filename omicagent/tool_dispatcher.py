"""工具调度器 (Tool Dispatcher).

Agent 与外部资源交互的统一网关, 封装三类工具:
1. OmicSeek 数据检索: 自然语言 -> 查询参数 -> 解析 JSON 结果 (文献+数据下载链接)
2. DCS Cloud 容器/Shell 命令: 在项目空间执行命令/管理文件/跑分析
3. 训推平台大模型: 统一 HTTP 客户端 (转调 llm_client), 含重试/Token计数/Context Cache
"""
from __future__ import annotations
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from . import config
from .llm_client import LLMClient

log = logging.getLogger("omicagent.tools")


@dataclass
class ShellResult:
    cmd: str
    returncode: int
    stdout: str
    stderr: str
    success: bool

    def __str__(self) -> str:
        return f"[{self.returncode}] {self.cmd}\n{self.stdout[-2000:]}"


class ToolDispatcher:
    def __init__(self, llm: Optional[LLMClient] = None, workdir: Optional[Path] = None):
        self.llm = llm or LLMClient()
        self.workdir = Path(workdir) if workdir else config.RESULTS_DIR
        self.workdir.mkdir(parents=True, exist_ok=True)

    # ---------- 1. 数据检索 (委托 DataSearcher, 主通道 NCBI GEO) ----------
    def omicseek_search(self, query: str, topk: int = 5) -> dict:
        """自然语言检索组学数据集 (多源: NCBI GEO 主 + ArrayExpress/OmicSeek 辅).

        返回 SearchReport.to_dict(): 含解析查询 + 去重重排后的记录列表 + 下载链接.
        """
        # 延迟导入避免循环依赖
        from .data_searcher import DataSearcher
        searcher = DataSearcher(self.llm)
        report = searcher.search(query, topk=topk)
        log.info("数据检索完成: %d 条记录, 耗时 %.1fs, 可用源=%s",
                 len(report.records), report.elapsed, report.sources_ok)
        return report.to_dict()

    # ---------- 2. Shell / 容器命令执行 ----------
    def run_shell(self, cmd: str, timeout: int = 1800, cwd: Optional[Path] = None,
              env: Optional[dict] = None, warn_on_failure: bool = True) -> ShellResult:
        """在分析环境内执行 Shell 命令 (带超时与日志)."""
        log.info("执行命令: %s", cmd)
        run_cwd = str(cwd) if cwd else str(self.workdir)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=run_cwd, capture_output=True, text=True,
                timeout=timeout, env=env, executable="/bin/bash",
            )
            ok = proc.returncode == 0
            if not ok and warn_on_failure:
                log.warning("命令失败 [%s]: %s", proc.returncode, proc.stderr[-500:])
            return ShellResult(cmd, proc.returncode, proc.stdout, proc.stderr, ok)
        except subprocess.TimeoutExpired:
            return ShellResult(cmd, -1, "", f"超时 ({timeout}s)", False)
        except Exception as e:
            return ShellResult(cmd, -1, "", str(e), False)

    def run_script_file(self, script_path: Path, runner: str = "bash",
                        timeout: int = 3600) -> ShellResult:
        """执行一个脚本文件."""
        if not script_path.exists():
            return ShellResult(str(script_path), -1, "", "脚本不存在", False)
        runner_bin = shutil.which(runner) or f"/usr/bin/{runner}"
        return self.run_shell(f"{runner_bin} {script_path}", timeout=timeout)

    # ---------- 3. LLM 调用 (转调 llm_client) ----------
    def llm_call(self, prompt: str, task_type: str = "simple", **kwargs) -> str:
        """统一的大模型调用入口, 返回文本."""
        return self.llm.complete(prompt, task_type=task_type, **kwargs).content

    def parse_metadata(self, dataset_info: str) -> dict:
        """用 LLM 解析数据集元信息, 判断所需分析工具与环境配置 (能力2 前置)."""
        resp = self.llm.complete_json(
            f"解析以下单细胞数据集元信息, 输出 JSON: "
            f"species(物种), n_cells(估计细胞数), modality(技术如 snRNA-seq), "
            f"format(文件格式), required_tools(所需工具列表), env_plan(环境配置建议).\n"
            f"元信息:\n{dataset_info}",
            task_type="complex", max_tokens=1024,
        )
        return resp

    def parse_obs_semantics(self, adata, paper_text: str = "") -> dict:
        """语义理解 obs 字段: 识别列语义 + 映射标准注释 (能力3, 委托 MetadataParser)."""
        from .metadata_parser import MetadataParser
        from .ontology import load_ontology
        mp = MetadataParser(self.llm)
        return mp.summarize(adata, ontology=load_ontology("plant_leaf"), paper_text=paper_text)
