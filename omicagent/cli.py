"""OmicAgent 对话式 CLI (类 Claude Code).

启动: 终端输入 `omicagent`
首次启动交互配置 API/模型, 之后进入对话 REPL, Agent 自动调用工具完成数据检索与跨物种分析.
"""
from __future__ import annotations
import sys, os, logging
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.live import Live
from rich.text import Text

from . import config
from .config_manager import load_config, first_run_setup, edit_config, save_config, UserConfig
from .llm_client import LLMClient
from .tools import ToolRegistry
from .agent import AgentLoop
from .tool_dispatcher import ToolDispatcher

console = Console()


def main():
    # 日志到文件, 不污染终端
    os.makedirs(os.path.expanduser("~/.omicagent"), exist_ok=True)
    logging.basicConfig(
        filename=os.path.expanduser("~/.omicagent/omicagent.log"),
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # 1. 加载/引导配置
    cfg = load_config()
    if cfg is None:
        cfg = first_run_setup(console)
    _apply_config(cfg)

    # 2. 初始化组件
    llm = LLMClient()
    dispatcher = ToolDispatcher(llm)
    registry = ToolRegistry(llm, dispatcher)
    agent = AgentLoop(llm, registry, max_rounds=cfg.max_tool_rounds,
                      on_event=lambda k, d: _render_event(k, d))

    # 3. REPL
    _banner(cfg)
    while True:
        try:
            user_input = console.input("\n[bold cyan]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见[/]")
            break
        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_slash(user_input, cfg, agent):
                break
            continue
        try:
            agent.run(user_input)
        except Exception as e:
            console.print(f"[red]出错: {e}[/]")


def _apply_config(cfg: UserConfig):
    """把用户配置注入 config 模块 (供 LLMClient 等使用)."""
    os.environ["OMICAGENT_API_BASE"] = cfg.api_base
    os.environ["OMICAGENT_API_KEY"] = cfg.api_key
    os.environ["OMICAGENT_SIMPLE_MODEL"] = cfg.simple_model
    os.environ["OMICAGENT_COMPLEX_MODEL"] = cfg.complex_model
    os.environ["OMICAGENT_COMPLEX_FALLBACK"] = cfg.fallback_model
    os.environ["OMICAGENT_DATA_DIR"] = cfg.data_dir
    # 重载 config 模块变量
    import importlib
    from . import config as cfg_mod
    importlib.reload(cfg_mod)
    # 确保 RESULTS_DIR 存在
    os.makedirs(cfg.results_dir, exist_ok=True)


def _banner(cfg: UserConfig):
    console.print(Panel.fit(
        f"[bold cyan]OmicAgent[/] v0.2.0  植物单细胞组学 AI Scientist\n"
        f"[dim]模型:[/] {cfg.complex_model} (复杂) / {cfg.simple_model} (简单)\n"
        f"[dim]数据目录:[/] {cfg.data_dir}\n"
        f"[dim]输入需求开始对话, /help 查看命令, /exit 退出[/]",
        border_style="cyan"))


def _render_event(kind: str, data: dict):
    """渲染 Agent 事件到终端 (类 Claude Code 工具调用展示)."""
    if kind == "reasoning":
        # 推理模型思考流, 灰色小字
        console.print(Text(data["text"], style="dim italic"), end="")
    elif kind == "thought":
        console.print(f"\n[blue]💭 {data['text']}[/]")
    elif kind == "tool_call":
        args_str = ", ".join(f"{k}={v}" for k, v in data["args"].items()) if data["args"] else ""
        console.print(Panel(f"[cyan]🔧 {data['tool']}[/]({args_str})",
                            border_style="cyan", expand=False))
    elif kind == "tool_result":
        text = data["result"]
        # 截断长结果
        if len(text) > 600:
            text = text[:600] + " ..."
        color = "green" if not text.startswith(("错误", "工具")) and "出错" not in text else "yellow"
        console.print(Text(text, style=color), style=color)
    elif kind == "answer":
        console.print()  # 换行
        console.print(Markdown(data["text"]))
    elif kind == "error":
        console.print(f"[red]{data['text']}[/]")


def _handle_slash(cmd: str, cfg: UserConfig, agent: AgentLoop) -> bool:
    """处理斜杠命令, 返回 True 表示退出."""
    parts = cmd.split(maxsplit=1)
    c = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if c in ("/exit", "/quit", "/q"):
        return True
    if c == "/help":
        console.print(Panel(
            "[cyan]/help[/] 显示帮助\n"
            "[cyan]/model [name][/] 切换复杂模型\n"
            "[cyan]/config[/] 编辑配置 (API/模型)\n"
            "[cyan]/data [path][/] 设置/查看数据目录\n"
            "[cyan]/clear[/] 清空对话历史\n"
            "[cyan]/tools[/] 列出可用工具\n"
            "[cyan]/exit[/] 退出",
            title="命令", border_style="cyan"))
    elif c == "/model":
        if arg:
            cfg.complex_model = arg
            save_config(cfg)
            _apply_config(cfg)
            console.print(f"[green]✓ 复杂模型切换为 {arg}[/]")
        else:
            console.print(f"当前: 复杂={cfg.complex_model}, 简单={cfg.simple_model}, 回退={cfg.fallback_model}")
    elif c == "/config":
        edit_config(console, cfg)
        _apply_config(cfg)
    elif c == "/data":
        if arg:
            cfg.data_dir = arg
            save_config(cfg)
            os.environ["OMICAGENT_DATA_DIR"] = arg
            console.print(f"[green]✓ 数据目录: {arg}[/]")
        else:
            console.print(f"数据目录: {cfg.data_dir}")
            # 列本地数据
            registry = ToolRegistry()
            res = registry._list_local_data()
            for h in res["local_h5ad"]:
                console.print(f"  {h}")
    elif c == "/clear":
        agent.history.clear()
        console.print("[green]✓ 已清空对话历史[/]")
    elif c == "/tools":
        registry = ToolRegistry()
        console.print(registry.schema_for_llm())
    else:
        console.print(f"[yellow]未知命令: {c} (/help 查看命令)[/]")
    return False


if __name__ == "__main__":
    main()
