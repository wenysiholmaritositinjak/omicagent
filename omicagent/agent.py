"""Agent 工具循环 (类 Claude Code 的 tool use loop).

用户输入 → LLM 思考(流式) → 输出 tool_call JSON 或最终回答 → 执行工具 → 结果喂回 → 循环.
工具调用用提示词 JSON 方式 (通用, 适配推理模型).
"""
from __future__ import annotations
import json, logging, re
from typing import Optional, Callable

from .llm_client import LLMClient
from .tools import ToolRegistry

log = logging.getLogger("omicagent.agent")

SYSTEM_PROMPT = """你是 OmicAgent, 一个植物单细胞组学 AI 科学家助手. 通过调用工具帮用户完成数据检索、元数据解析、环境搭建、分析模块运行与跨物种整合.

# 可用工具
{tools_schema}

# 工作流程
1. 理解用户需求, 思考需要哪些工具, 按顺序调用
2. 每次只调用一个工具, 等结果返回后再决定下一步
3. 跨物种需求(如"整合水稻和拟南芥")自动用 run_cross_species; 检索数据用 search_data; 解析注释用 parse_metadata
4. 工具结果会以 user 角色返回给你, 据此继续
5. 完成后用自然语言总结结果, 不要再调用工具

# 数据检索报告规则 (重要)
search_data 返回每条的 data_type/total_size/has_processed/files/organs. 向用户报告时必须:
- 列出每条的 数据类型(processed/matrix/raw/archive)、文件大小、是否处理好的rds/h5ad
- 图谱类数据(若 organs 非空)必须列出包含的所有器官, 提示用户"虽标题未提某器官但数据实际包含"
- 优先推荐 processed(处理好的rds/h5ad, 开箱即用), 其次 matrix(需构建对象), 最后 raw(测序原始)
- 下载时 download_data 默认 file_type=processed; 若返回 need_confirm=True(超过5G) 必须告知用户预估大小并等待确认, 不得自行继续下载

# 检索策略 (重要)
- 用户说"植物"但未指定物种时, search_data 会自动对多个候选物种(拟南芥/水稻/玉米等)分别检索合并, 勿只搜"plant"关键词
- 检索某器官(如胚胎)时, 即使数据集标题写"atlas"未提该器官, 只要 organs 字段含该器官也算命中, 应推荐给用户

# 数据库目录优先 (加快检索)
检索数据前, 若用户问"有哪些数据库/能从哪查", 调 list_databases (可按物种过滤) 展示已知库目录.
检索流程: 优先查 catalog 里有 API 的库(GEO/PubMed/ArrayExpress/HCA 等已内置, 快) → 再网络补充.
用户要求更新库目录时调 update_database_catalog (无参=刷新可用性, 带参=追加新库).

# 语言
所有 thought 与 answer 必须使用 {language} 语言. (工具名/参数/代码保持原样, 不翻译)

# Ponytail 懒惰阶梯 (生成/运行脚本时遵循 — 写最少必要代码)
调用 run_module 或任何生成代码的工具时, 遵循懒惰阶梯 (命中即停):
1. 这步需要存在吗? 不需要跳过 (YAGNI)
2. 代码库/scanpy/seurat 已有? 复用别重写
3. 标准库能做? 用标准库
4. 已装依赖能做? 用它, 不引新包
5. 一行能搞定? 一行
6. 最后才写: 能工作的最少代码
永不砍: 验证/错误处理/安全/路径处理/限速. 懒惰是对解决方案懒, 不是对读代码懒.

# 输出格式 (严格)
每轮输出一个 JSON 对象, 不要 markdown 围栏, 不要多余文字:
- 调用工具: {{"thought":"简短思考","action":"tool_call","tool":"工具名","args":{{...}}}}
- 最终回答: {{"thought":"简短思考","action":"answer","answer":"给用户的自然语言回答(可含markdown)"}}

注意: answer 中可包含关键结果摘要、文件路径、下一步建议."""


class AgentLoop:
    def __init__(self, llm: LLMClient, registry: ToolRegistry,
                 max_rounds: int = 10, on_event: Optional[Callable] = None,
                 language: str = "中文"):
        self.llm = llm
        self.registry = registry
        self.max_rounds = max_rounds
        self.on_event = on_event or (lambda kind, data: None)
        self.language = language
        self.history: list[dict] = []

    def run(self, user_input: str) -> str:
        """执行一轮对话, 返回最终回答文本."""
        self.history.append({"role": "user", "content": user_input})
        sys = SYSTEM_PROMPT.format(tools_schema=self.registry.schema_for_llm(),
                                   language=self.language)

        for round_i in range(1, self.max_rounds + 1):
            self.on_event("round", {"round": round_i})
            # 流式获取 LLM 输出
            full = self._stream_llm(sys)
            self.history.append({"role": "assistant", "content": full})

            parsed = _parse_agent_output(full)
            if parsed is None:
                # 无法解析为 JSON, 当作最终回答
                self.on_event("answer", {"text": full})
                return full

            action = parsed.get("action", "answer")
            thought = parsed.get("thought", "")
            if thought:
                self.on_event("thought", {"text": thought})

            if action == "answer":
                answer = parsed.get("answer", full)
                self.on_event("answer", {"text": answer})
                return answer

            if action == "tool_call":
                tool = parsed.get("tool", "")
                args = parsed.get("args", {}) or {}
                self.on_event("tool_call", {"tool": tool, "args": args})
                result = self.registry.execute(tool, args)
                self.on_event("tool_result", {"tool": tool, "result": result})
                # 工具结果喂回 LLM
                self.history.append({"role": "user",
                                     "content": f"[工具 {tool} 执行结果]\n{result}\n\n请根据结果继续 (调用下一工具或给出最终回答)."})
                continue

            # 未知 action
            self.history.append({"role": "user", "content": f"未识别 action: {action}, 请输出 tool_call 或 answer."})

        self.on_event("answer", {"text": "已达到最大工具调用轮数, 请缩小需求范围或继续对话."})
        return "已达到最大工具调用轮数."

    def _stream_llm(self, system: str) -> str:
        """流式调用 LLM, 实时输出 reasoning/content, 返回完整 content."""
        parts = []
        try:
            for kind, token in self.llm.complete_stream(
                prompt="", task_type="complex", system=system, history=self.history,
                max_tokens=4096, temperature=0.3,
            ):
                if kind == "reasoning":
                    self.on_event("reasoning", {"text": token})
                elif kind == "content":
                    parts.append(token)
                    self.on_event("content", {"text": token})
                elif kind == "done":
                    # content 已在流中累积, done 不重复加 (仅无 content 时兜底)
                    if token and not parts:
                        parts.append(token)
        except Exception as e:
            log.exception("LLM 流式失败")
            self.on_event("error", {"text": str(e)})
            # 退化为非流式
            resp = self.llm.complete("", task_type="complex", system=system, history=self.history)
            parts = [resp.content]
        return "".join(parts)


def _parse_agent_output(text: str) -> dict | None:
    """从 LLM 输出提取 JSON (容错: 去 markdown 围栏, 提取首个平衡 {...})."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # 直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 提取首个平衡的 JSON 对象 (避免贪婪匹配多个 {})
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return None
