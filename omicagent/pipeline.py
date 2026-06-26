"""OmicAgent 编排层 (Pipeline).

"理解 -> 规划 -> 执行 -> 输出" 闭环:
TaskPlanner 规划 -> ToolDispatcher 调度执行各任务 -> ReportGenerator 输出 HTML.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional

from . import config
from .llm_client import LLMClient
from .task_planner import TaskPlanner, Plan
from .tool_dispatcher import ToolDispatcher
from .code_generator import CodeGenerator
from .report_generator import ReportGenerator
from .data_searcher import DataSearcher
from .metadata_parser import MetadataParser
from .env_builder import EnvBuilder
from .ontology import load_ontology

log = logging.getLogger("omicagent.pipeline")


class OmicAgent:
    def __init__(self):
        self.llm = LLMClient()
        self.planner = TaskPlanner(self.llm)
        self.dispatcher = ToolDispatcher(self.llm)
        self.codegen = CodeGenerator(self.llm, self.dispatcher)
        self.reporter = ReportGenerator(self.llm)
        self.searcher = DataSearcher(self.llm)
        self.metadata_parser = MetadataParser(self.llm)
        self.env_builder = EnvBuilder(self.llm, self.dispatcher)
        self.run_log: list[dict] = []

    def run(self, user_input: str, context: str = "", render_report: bool = True) -> dict:
        """端到端执行一次自然语言分析需求."""
        log.info("=== OmicAgent 启动 ===\n需求: %s", user_input)

        # 1. 理解 + 规划
        plan: Plan = self.planner.plan(user_input, context=context)
        self.run_log.append({"stage": "plan", "data": plan.to_dict()})

        # 2. 执行
        results = []
        for task in plan.tasks:
            log.info("任务 %d: %s [tool=%s module=%s]", task.id, task.description, task.tool, task.module)
            res = self._execute_task(task, plan)
            results.append({"task_id": task.id, "description": task.description,
                            "module": task.module, "result": res})
            self.run_log.append({"stage": "execute", "task_id": task.id, "result": res})

        # 3. 输出
        out = {"goal": plan.goal, "plan": plan.to_dict(), "results": results,
               "usage": self.llm.total_usage()}
        if render_report:
            out["report"] = str(self._render(plan, results, user_input))
        return out

    def _execute_task(self, task, plan: Plan) -> str:
        try:
            if task.tool == "omicseek":
                r = self.dispatcher.omicseek_search(task.description)
                return json.dumps(r, ensure_ascii=False)[:4000]
            if task.tool == "llm":
                return self.dispatcher.llm_call(task.description, task_type="complex")
            if task.tool == "code_gen":
                lang = task.params.get("lang", "python")
                res = self.codegen.generate_and_run(
                    task.module or "qc", lang=lang, params=task.params,
                    data_info=task.params.get("data_info", ""), task_desc=task.description,
                )
                return f"success={res.success}\nstdout:\n{res.stdout[-2000:]}\nstderr:\n{res.stderr[-1000:]}"
            if task.tool == "shell":
                res = self.dispatcher.run_shell(task.description)
                return f"success={res.success}\n{res.stdout[-2000:]}"
            if task.tool == "report":
                return "report stage (deferred to render)"
            return f"未知工具: {task.tool}"
        except Exception as e:
            log.exception("任务 %d 异常", task.id)
            return f"ERROR: {e}"

    def _render(self, plan: Plan, results: list, user_input: str) -> Path:
        summary = self.reporter.summarize(
            json.dumps(results, ensure_ascii=False)[:6000], goal=plan.goal
        )
        code_blocks = []
        scripts_dir = config.RESULTS_DIR / "scripts"
        if scripts_dir.exists():
            for p in sorted(scripts_dir.glob("*")):
                code_blocks.append({"title": p.name, "lang": p.suffix.lstrip("."),
                                    "code": p.read_text(encoding="utf-8", errors="ignore")[:4000]})
        usage = self.llm.total_usage()
        return self.reporter.render(
            title=f"OmicAgent 分析报告 — {plan.goal[:40]}",
            goal=plan.goal,
            summary=summary,
            code_blocks=code_blocks,
            run_info={
                "planner_model": config.COMPLEX_MODEL,
                "codegen_model": config.SIMPLE_MODEL,
                "llm_calls": usage["calls"],
                "total_tokens": usage["total_tokens"],
                "extra": {"用户需求": user_input[:80]},
            },
        )

    # ===================== 端到端: 三能力串联 =====================
    def search_and_analyze(self, user_input: str, local_data: str = "",
                           render_report: bool = True) -> dict:
        """端到端: 数据检索 -> 元数据语义解析 -> 自动建环境 -> 生成运行脚本 -> 报告.

        local_data: 若提供本地 h5ad 路径, 跳过下载, 直接用于能力3/4 (便于用已有数据验证).
        """
        log.info("=== OmicAgent 端到端 ===\n需求: %s", user_input)
        out = {"user_input": user_input, "stages": {}, "usage": {}}
        try:
            usage0 = self.llm.total_usage()

            # 1. 能力1: 数据检索
            report = self.searcher.search(user_input, topk=5)
            out["stages"]["search"] = report.to_dict()
            log.info("[1/4] 检索完成: %d 条, 耗时 %.1fs", len(report.records), report.elapsed)

            # 2. 能力3: 元数据语义解析 (用本地数据或检索首条的本地缓存)
            mapping_info = None
            adata = None
            data_path = local_data
            if data_path:
                try:
                    adata = self.metadata_parser.load(data_path)
                    ins = self.metadata_parser.inspect_columns(adata)
                    mapping_info = {"inspection": ins.to_dict()}
                    if ins.celltype_col:
                        # 取文献摘要(若有 pubmed id)
                        paper = ""
                        if report.records and report.records[0].pubmed_id:
                            paper = self.metadata_parser.fetch_paper_context(report.records[0].pubmed_id)
                        mr = self.metadata_parser.map_to_standard(adata, ins.celltype_col,
                                                                  load_ontology("plant_leaf"), paper)
                        mapping_info["mapping"] = mr.to_dict()
                        adata = self.metadata_parser.apply_mapping(adata, ins.celltype_col, mr)
                    out["stages"]["metadata"] = mapping_info
                    log.info("[2/4] 元数据解析: celltype_col=%s", ins.celltype_col)
                except Exception as e:
                    log.warning("元数据解析失败: %s", e)
                    out["stages"]["metadata"] = {"error": str(e)}

            # 3. 能力2: 自动建环境
            meta_for_env = {"species": report.parsed.get("species", ""),
                            "modality": report.parsed.get("modality", "")}
            if mapping_info and "inspection" in mapping_info:
                meta_for_env["n_cells"] = getattr(adata, "n_obs", 0) if adata else 0
                meta_for_env["format"] = "h5ad"
            env_spec = self.env_builder.analyze(meta_for_env, analysis_goal=user_input)
            env_result = self.env_builder.build(env_spec)
            out["stages"]["env"] = env_result.to_dict()
            log.info("[3/4] 环境: env=%s success=%s", env_spec.env_name, env_result.success)

            # 4. 代码生成 + 运行 (绑定 env)
            if adata is not None:
                data_info = (f"h5ad 路径: {data_path}, {adata.n_obs} 细胞 x {adata.n_vars} 基因, "
                             f"细胞类型列: {mapping_info.get('inspection',{}).get('celltype_col','') if mapping_info else ''}")
                res = self.codegen.generate_and_run("qc", lang="python", data_info=data_info,
                                                    task_desc=user_input, env=env_spec.env_name)
                out["stages"]["analysis"] = {"success": res.success,
                                             "stdout": res.stdout[-1500:], "stderr": res.stderr[-800:]}
                log.info("[4/4] 分析脚本: success=%s", res.success)

            out["usage"] = self.llm.total_usage()
            out["usage"]["delta_tokens"] = out["usage"]["total_tokens"] - usage0.get("total_tokens", 0)

            if render_report:
                out["report"] = str(self._render_e2e(out, user_input))
        except Exception as e:
            log.exception("端到端异常")
            out["error"] = str(e)
        return out

    def _render_e2e(self, out: dict, user_input: str) -> Path:
        """端到端报告: 检索结果表 + 元数据映射表 + 环境信息 + 分析输出."""
        tables = []
        # 检索结果表
        if out.get("stages", {}).get("search", {}).get("records"):
            rows = [{"accession": r["accession"], "source": r["source_db"],
                     "species": r["species"], "n_samples": r["n_samples"],
                     "title": r["title"][:60], "relevance": round(r["relevance"], 2),
                     "download": r["download_url"]}
                    for r in out["stages"]["search"]["records"]]
            tables.append({"title": "检索结果", "columns": list(rows[0].keys()) if rows else [],
                           "rows": rows})
        # 映射表
        mp = out.get("stages", {}).get("metadata", {}).get("mapping")
        if mp:
            rows = [{"original": k, "standard": v, "confidence": round(mp["confidence"].get(k, 0), 2)}
                    for k, v in mp["mapping"].items()]
            tables.append({"title": f"细胞类型映射 (覆盖 {mp['coverage']:.0%})", "columns": list(rows[0].keys()) if rows else [], "rows": rows})
        usage = self.llm.total_usage()
        summary = self.reporter.summarize(
            json.dumps(out.get("stages", {}), ensure_ascii=False)[:6000],
            goal=user_input[:80])
        return self.reporter.render(
            title=f"OmicAgent 端到端报告 — {user_input[:40]}",
            goal=user_input, summary=summary, tables=tables,
            run_info={"env": out.get("stages", {}).get("env", {}).get("spec", {}).get("env_name", ""),
                      "llm_calls": usage["calls"], "total_tokens": usage["total_tokens"],
                      "extra": {"用户需求": user_input[:80],
                                "检索记录数": out.get("stages", {}).get("search", {}).get("n_records", 0),
                                "环境": out.get("stages", {}).get("env", {}).get("spec", {}).get("env_name", "")}},
        )
