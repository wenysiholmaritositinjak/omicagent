"""非交互测试 Agent 工具循环 (验证对话式 CLI 的 agent 逻辑).

模拟对话: 直接调 AgentLoop.run(), 不进 REPL. 验证 LLM→工具调用→结果→回答循环.
"""
import sys, os
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.config_manager import load_config
from omicagent.llm_client import LLMClient
from omicagent.tools import ToolRegistry
from omicagent.tool_dispatcher import ToolDispatcher
from omicagent.agent import AgentLoop

def main():
    cfg = load_config()
    # 注入环境变量
    for k, v in [("OMICAGENT_API_BASE", cfg.api_base), ("OMICAGENT_API_KEY", cfg.api_key),
                 ("OMICAGENT_SIMPLE_MODEL", cfg.simple_model),
                 ("OMICAGENT_COMPLEX_MODEL", cfg.complex_model),
                 ("OMICAGENT_COMPLEX_FALLBACK", cfg.fallback_model),
                 ("OMICAGENT_DATA_DIR", cfg.data_dir)]:
        os.environ[k] = v
    import importlib
    from omicagent import config as cfg_mod
    importlib.reload(cfg_mod)

    llm = LLMClient()
    dispatcher = ToolDispatcher(llm)
    registry = ToolRegistry(llm, dispatcher)

    # 事件打印
    def on_event(kind, data):
        if kind == "thought":
            print(f"\n[思考] {data['text']}")
        elif kind == "tool_call":
            print(f"\n[工具调用] {data['tool']}({data['args']})")
        elif kind == "tool_result":
            print(f"[工具结果] {data['result'][:300]}")
        elif kind == "answer":
            print(f"\n[最终回答]\n{data['text']}")

    agent = AgentLoop(llm, registry, max_rounds=8, on_event=on_event)

    # 测试1: 检索 (应触发 search_data)
    print("=" * 60)
    print("测试1: 数据检索对话")
    print("=" * 60)
    ans = agent.run("帮我找拟南芥叶片单细胞数据，包含气孔细胞")
    print("\n--- 回答结束 ---")

    # 测试2: 列本地数据 (应触发 list_local_data)
    print("\n" + "=" * 60)
    print("测试2: 列本地数据")
    print("=" * 60)
    agent.history.clear()
    ans2 = agent.run("列出本地可用的单细胞数据文件")
    print("\n--- 完成 ---")

if __name__ == "__main__":
    main()
