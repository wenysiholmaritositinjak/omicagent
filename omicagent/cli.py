"""OmicAgent 对话式 CLI (类 Claude Code).

启动: 终端输入 `omicagent`
首次启动交互配置 API/模型, 之后进入对话 REPL, Agent 自动调用工具完成数据检索与跨物种分析.
"""
from __future__ import annotations
import sys, os, logging
from typing import Optional

# 强制 stdout/stderr UTF-8, 避免中文/emoji 乱码 (WSL locale 为 C 时 stdout 编码会退化为 ASCII)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from rich.console import Console
from rich.panel import Panel
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.formatted_text import HTML
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.live import Live
from rich.text import Text

from . import config
from .config_manager import (load_config, first_run_setup, edit_config, save_config,
                             UserConfig, LANGUAGES)
from .llm_client import LLMClient
from .tools import ToolRegistry
from .agent import AgentLoop
from .tool_dispatcher import ToolDispatcher

console = Console()

# 界面文案多语言 (轻量 i18n)
I18N = {
    "中文": {"tagline": "植物单细胞组学 AI Scientist",
             "hint": "输入需求开始对话, /help 查看命令, /exit 退出",
             "model": "模型", "data": "数据目录", "lang": "语言",
             "lang_changed": "✓ 语言切换为", "unknown_cmd": "未知命令", "see_help": "(/help 查看命令)"},
    "English": {"tagline": "Plant Single-cell Omics AI Scientist",
                "hint": "Type your request, /help for commands, /exit to quit",
                "model": "Model", "data": "Data dir", "lang": "Language",
                "lang_changed": "✓ Language switched to", "unknown_cmd": "Unknown command", "see_help": "(/help for commands)"},
    "日本語": {"tagline": "植物シングルセルオミクス AI Scientist",
              "hint": "要求を入力, /help でコマンド, /exit で終了",
              "model": "モデル", "data": "データ_dir", "lang": "言語",
              "lang_changed": "✓ 言語を切り替えました:", "unknown_cmd": "不明なコマンド", "see_help": "(/help でコマンド)"},
    "한국어": {"tagline": "식물 단일세포 오믹스 AI Scientist",
             "hint": "요청 입력, /help 명령어, /exit 종료",
             "model": "모델", "data": "데이터 디렉토리", "lang": "언어",
             "lang_changed": "✓ 언어 변경:", "unknown_cmd": "알 수 없는 명령", "see_help": "(/help 명령어)"},
    "Français": {"tagline": "AI Scientist en omique monocellulaire végétale",
                "hint": "Saisissez votre demande, /help pour commandes, /exit pour quitter",
                "model": "Modèle", "data": "Répertoire données", "lang": "Langue",
                "lang_changed": "✓ Langue changée à", "unknown_cmd": "Commande inconnue", "see_help": "(/help pour commandes)"},
    "Deutsch": {"tagline": "Pflanzliche Single-Cell Omics AI Scientist",
               "hint": "Anfrage eingeben, /help für Befehle, /exit zum Beenden",
               "model": "Modell", "data": "Datenverzeichnis", "lang": "Sprache",
               "lang_changed": "✓ Sprache geändert auf", "unknown_cmd": "Unbekannter Befehl", "see_help": "(/help für Befehle)"},
    "Español": {"tagline": "AI Scientist de ómica unicelular vegetal",
               "hint": "Escriba su solicitud, /help para comandos, /exit para salir",
               "model": "Modelo", "data": "Directorio de datos", "lang": "Idioma",
               "lang_changed": "✓ Idioma cambiado a", "unknown_cmd": "Comando desconocido", "see_help": "(/help para comandos)"},
    "Português": {"tagline": "AI Scientist de ômica de célula única vegetal",
                 "hint": "Digite seu pedido, /help para comandos, /exit para sair",
                 "model": "Modelo", "data": "Diretório de dados", "lang": "Idioma",
                 "lang_changed": "✓ Idioma alterado para", "unknown_cmd": "Comando desconhecido", "see_help": "(/help para comandos)"},
    "Русский": {"tagline": "AI Scientist по растительной одноклеточной омике",
               "hint": "Введите запрос, /help для команд, /exit для выхода",
               "model": "Модель", "data": "Каталог данных", "lang": "Язык",
               "lang_changed": "✓ Язык изменён на", "unknown_cmd": "Неизвестная команда", "see_help": "(/help для команд)"},
}


def _t(cfg: UserConfig, key: str) -> str:
    """取当前语言文案."""
    return I18N.get(cfg.language, I18N["中文"]).get(key, I18N["中文"][key])


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
                      language=cfg.language,
                      on_event=lambda k, d: _render_event(k, d))

    # 3. REPL (用 prompt_toolkit 支持光标移动/历史/删除等行编辑)
    _banner(cfg)
    session = PromptSession(history=InMemoryHistory())
    while True:
        try:
            user_input = session.prompt(HTML("<ansicyan><b>&gt;</b></ansicyan> ")).strip()
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
        f"[bold cyan]OmicAgent[/] v0.2.0  {_t(cfg, 'tagline')}\n"
        f"[dim]{_t(cfg, 'model')}:[/] {cfg.complex_model} (complex) / {cfg.simple_model} (simple)\n"
        f"[dim]{_t(cfg, 'data')}:[/] {cfg.data_dir}\n"
        f"[dim]{_t(cfg, 'lang')}:[/] {cfg.language}\n"
        f"[dim]{_t(cfg, 'hint')}[/]",
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
            "[cyan]/lang [语言][/] 切换界面与回复语言 (中文/English/日本語/한국어/Français/Deutsch/Español/Português/Русский)\n"
            "[cyan]/config[/] 编辑配置 (API/模型/语言)\n"
            "[cyan]/data [path][/] 设置/查看数据目录\n"
            "[cyan]/clear[/] 清空对话历史\n"
            "[cyan]/tools[/] 列出可用工具\n"
            "[cyan]/exit[/] 退出",
            title="命令", border_style="cyan"))
    elif c == "/lang":
        if arg:
            # 直接指定语言名
            lang = arg.strip()
            if lang in LANGUAGES:
                cfg.language = lang
                agent.language = lang
                save_config(cfg)
                console.print(f"[green]{_t(cfg, 'lang_changed')} {lang}[/]")
            else:
                console.print(f"[yellow]不支持的语言: {lang}. 可选: {', '.join(LANGUAGES)}[/]")
        else:
            console.print(f"当前语言: [cyan]{cfg.language}[/]")
            for i, lg in enumerate(LANGUAGES, 1):
                mark = "✓" if lg == cfg.language else " "
                console.print(f"  {mark} [cyan]{i}.[/] {lg}")
            choice = Prompt.ask("选择", default=str(LANGUAGES.index(cfg.language) + 1), console=console)
            if choice.isdigit() and 1 <= int(choice) <= len(LANGUAGES):
                cfg.language = LANGUAGES[int(choice) - 1]
                agent.language = cfg.language
                save_config(cfg)
                console.print(f"[green]{_t(cfg, 'lang_changed')} {cfg.language}[/]")
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
        console.print(f"[yellow]{_t(cfg, 'unknown_cmd')}: {c} {_t(cfg, 'see_help')}[/]")
    return False


if __name__ == "__main__":
    main()
