"""报告生成器 (Report Generator).

将分析结果汇编为可交互 HTML 报告 (Jinja2 渲染):
分析摘要(LLM生成) + 关键图表 + 数据表格 + 可复现代码 + 运行信息.
"""
from __future__ import annotations
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config
from .llm_client import LLMClient

log = logging.getLogger("omicagent.report")


class ReportGenerator:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()
        self.env = Environment(
            loader=FileSystemLoader(str(config.TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        self.template = self.env.get_template("report.html.j2")

    @staticmethod
    def _img_to_b64(path: Path) -> str:
        if not path or not Path(path).exists():
            return ""
        return base64.b64encode(Path(path).read_bytes()).decode()

    def summarize(self, results_text: str, goal: str = "") -> str:
        """用简单模型对分析结果生成自然语言摘要."""
        resp = self.llm.complete(
            f"你是植物单细胞组学分析助手. 根据以下分析结果, 生成一段简洁中文摘要 "
            f"(3-5 句, 含主要发现与方法).\n目标: {goal}\n分析结果:\n{results_text}",
            task_type="simple", max_tokens=1024,
        )
        return resp.content.strip()

    def render(
        self,
        title: str,
        goal: str,
        summary: str,
        plots: Optional[list[dict]] = None,
        tables: Optional[list[dict]] = None,
        code_blocks: Optional[list[dict]] = None,
        run_info: Optional[dict] = None,
        out_path: Optional[Path] = None,
    ) -> Path:
        plots = plots or []
        # 图片转 base64 内嵌, 保证单文件可分享
        for p in plots:
            if p.get("path") and not p.get("image_b64"):
                p["image_b64"] = self._img_to_b64(Path(p["path"]))

        html = self.template.render(
            title=title,
            goal=goal,
            summary=summary,
            plots=plots,
            tables=tables or [],
            code_blocks=code_blocks or [],
            run_info=run_info or {},
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        out_path = Path(out_path) if out_path else config.RESULTS_DIR / "report.html"
        out_path.write_text(html, encoding="utf-8")
        log.info("报告已生成: %s", out_path)
        return out_path
