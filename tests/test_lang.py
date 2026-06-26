"""测试语言切换: language=English 时 Agent 用英文回复."""
import sys, os
sys.path.insert(0, os.path.expanduser("~/bioinfo/agent_framework"))
from omicagent.config_manager import load_config
from omicagent.llm_client import LLMClient
from omicagent.tools import ToolRegistry
from omicagent.tool_dispatcher import ToolDispatcher
from omicagent.agent import AgentLoop

cfg = load_config()
for k, v in [("OMICAGENT_API_BASE", cfg.api_base), ("OMICAGENT_API_KEY", cfg.api_key),
             ("OMICAGENT_COMPLEX_MODEL", cfg.complex_model), ("OMICAGENT_DATA_DIR", cfg.data_dir)]:
    os.environ[k] = v
import importlib
from omicagent import config as cfg_mod
importlib.reload(cfg_mod)

llm = LLMClient()
registry = ToolRegistry(llm, ToolDispatcher(llm))

def on_event(kind, data):
    if kind == "thought": print(f"\n[thought] {data['text']}")
    elif kind == "tool_call": print(f"\n[tool_call] {data['tool']}({data['args']})")
    elif kind == "answer": print(f"\n[answer]\n{data['text']}")

# 测试英文
print("=" * 50, "\nTEST: language=English\n", "=" * 50)
agent = AgentLoop(llm, registry, max_rounds=5, language="English", on_event=on_event)
agent.run("list my local single-cell data files")
