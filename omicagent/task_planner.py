"""任务规划器 (Task Planner).

将用户自然语言输入解析为结构化的分析任务序列.
调用复杂模型 (glm-5.2 -> claude-opus-4-8) 进行意图理解与流程规划.
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional

from .llm_client import LLMClient

log = logging.getLogger("omicagent.planner")

SYSTEM_PROMPT = """你是 OmicAgent 的任务规划器, 面向植物单细胞组学分析.
将用户的自然语言需求分解为有序的分析任务清单.

可用工具 (tool 字段取值):
- omicseek    : OmicSeek 数据检索 (自然语言 -> 数据集+下载链接)
- shell       : 在分析环境内执行 Shell/conda 命令 (装依赖/跑脚本)
- code_gen    : 代码生成器 (生成 Seurat(R) 或 Scanpy(Python) 分析脚本)
- llm         : 调用大模型做元数据解析/注释映射/摘要
- report      : 报告生成器 (生成 HTML 报告)

标准分析模块 (module 字段, 对应文档表3-3):
qc, normalize, cluster, annotate, coexpression, cross_species

输出格式: 严格 JSON, 形如
{
  "goal": "一句话目标",
  "tasks": [
    {"id": 1, "description": "...", "tool": "code_gen", "module": "qc",
     "params": {"lang": "python"}, "expected_output": "..."}
  ]
}
只输出 JSON, 不要额外说明."""


@dataclass
class Task:
    id: int
    description: str
    tool: str
    module: str = ""
    params: dict = None
    expected_output: str = ""

    def __post_init__(self):
        if self.params is None:
            self.params = {}

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Plan:
    goal: str
    tasks: list[Task]

    def to_dict(self) -> dict:
        return {"goal": self.goal, "tasks": [t.to_dict() for t in self.tasks]}


class TaskPlanner:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    def plan(self, user_input: str, context: Optional[str] = None) -> Plan:
        prompt = f"用户需求: {user_input}"
        if context:
            prompt += f"\n\n已知上下文:\n{context}"
        data = self.llm.complete_json(prompt, task_type="complex", system=SYSTEM_PROMPT,
                                      max_tokens=4096)
        tasks = []
        for t in data.get("tasks", []):
            tasks.append(Task(
                id=t.get("id", len(tasks) + 1),
                description=t.get("description", ""),
                tool=t.get("tool", ""),
                module=t.get("module", ""),
                params=t.get("params", {}) or {},
                expected_output=t.get("expected_output", ""),
            ))
        # 兜底: 若模型未给出任务, 给一个最小可执行规划
        if not tasks:
            tasks = [Task(1, user_input, "code_gen", "cross_species", {}, "分析结果")]
        plan = Plan(goal=data.get("goal", user_input), tasks=tasks)
        log.info("规划完成: %d 个任务, 目标=%s", len(tasks), plan.goal)
        return plan
