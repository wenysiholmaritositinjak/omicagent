"""配置管理: 首次启动引导 + ~/.omicagent/config.toml 读写.

提供类 Claude Code 的首次配置体验: 选 API 提供商 → 填 base/key → 选模型 → 测试连接.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import toml
import requests
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

CONFIG_DIR = Path.home() / ".omicagent"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# API 提供商预设
PROVIDERS = {
    "dcs": {
        "name": "DCS Cloud (大赛训推平台)",
        "base": "https://dcsapi.dcs.cloud/api/aigress/unified/v1",
        "models": ["deepseek-v4-pro", "glm-5.2", "claude-opus-4-8", "claude-sonnet-4-6"],
    },
    "openai": {
        "name": "OpenAI 官方",
        "base": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "o1-mini"],
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)",
        "base": "",
        "models": [],
    },
}


@dataclass
class UserConfig:
    provider: str = "dcs"
    api_base: str = ""
    api_key: str = ""
    simple_model: str = "deepseek-v4-pro"
    complex_model: str = "glm-5.2"
    fallback_model: str = "claude-opus-4-8"
    data_dir: str = str(Path.home() / "bioinfo" / "data")
    results_dir: str = str(Path.home() / "bioinfo" / "results")
    max_tool_rounds: int = 10

    def to_toml(self) -> str:
        return toml.dumps({
            "api": {"provider": self.provider, "base": self.api_base, "key": self.api_key},
            "models": {"simple": self.simple_model, "complex": self.complex_model,
                       "fallback": self.fallback_model},
            "paths": {"data_dir": self.data_dir, "results_dir": self.results_dir},
            "runtime": {"max_tool_rounds": self.max_tool_rounds},
        })


def load_config() -> Optional[UserConfig]:
    """加载用户配置, 不存在返回 None."""
    if not CONFIG_FILE.exists():
        return None
    try:
        data = toml.load(CONFIG_FILE)
        api = data.get("api", {})
        models = data.get("models", {})
        paths = data.get("paths", {})
        rt = data.get("runtime", {})
        return UserConfig(
            provider=api.get("provider", "dcs"),
            api_base=api.get("base", ""),
            api_key=api.get("key", ""),
            simple_model=models.get("simple", "deepseek-v4-pro"),
            complex_model=models.get("complex", "glm-5.2"),
            fallback_model=models.get("fallback", "claude-opus-4-8"),
            data_dir=paths.get("data_dir", ""),
            results_dir=paths.get("results_dir", ""),
            max_tool_rounds=rt.get("max_tool_rounds", 10),
        )
    except Exception:
        return None


def save_config(cfg: UserConfig) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(cfg.to_toml(), encoding="utf-8")
    return CONFIG_FILE


def test_connection(api_base: str, api_key: str, model: str) -> tuple[bool, str]:
    """测试 API 连接, 返回 (成功, 消息)."""
    try:
        r = requests.post(
            api_base.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                  "max_tokens": 20, "stream": False},
            timeout=30,
        )
        if r.status_code == 200:
            return True, "连接成功"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


def first_run_setup(console: Console) -> UserConfig:
    """首次启动交互引导."""
    console.print(Panel.fit(
        "[bold cyan]欢迎使用 OmicAgent[/]\n首次使用, 需配置模型与 API. (配置存于 ~/.omicagent/config.toml)",
        border_style="cyan"))
    console.print()

    # 1. 选提供商
    console.print("[bold]1. 选择 API 提供商:[/]")
    keys = list(PROVIDERS.keys())
    for i, k in enumerate(keys, 1):
        console.print(f"  [cyan]{i}.[/] {PROVIDERS[k]['name']}")
    choice = Prompt.ask("选择", default="1", console=console)
    provider = keys[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(keys) else "custom"
    preset = PROVIDERS[provider]

    # 2. API base
    api_base = Prompt.ask("2. API base", default=preset["base"], console=console)

    # 3. API key
    api_key = Prompt.ask("3. API key", password=True, console=console)

    # 4. 选模型 (从预设 + 手输)
    console.print("[bold]4. 选择模型 (简单任务用, 如代码生成/元数据解析):[/]")
    models = preset["models"]
    simple = _pick_model(console, models, "deepseek-v4-pro")
    console.print("[bold]5. 选择模型 (复杂任务用, 如规划/注释映射):[/]")
    complex_m = _pick_model(console, models, "glm-5.2")
    console.print("[bold]6. 选择回退模型 (复杂任务主模型失败时):[/]")
    fallback = _pick_model(console, models, "claude-opus-4-8")

    cfg = UserConfig(provider=provider, api_base=api_base, api_key=api_key,
                     simple_model=simple, complex_model=complex_m, fallback_model=fallback)

    # 7. 测试连接
    console.print("\n[bold]7. 测试连接...[/]")
    ok, msg = test_connection(api_base, api_key, complex_m)
    if ok:
        console.print(f"  [green]✓ {msg}[/]")
    else:
        console.print(f"  [yellow]⚠ {msg}[/]")
        if not Confirm.ask("连接失败, 仍保存配置?", default=True, console=console):
            return first_run_setup(console)

    save_config(cfg)
    console.print(f"\n[green]✓ 配置已保存: {CONFIG_FILE}[/]\n")
    return cfg


def _pick_model(console: Console, presets: list[str], default: str) -> str:
    if presets:
        for i, m in enumerate(presets, 1):
            console.print(f"  [cyan]{i}.[/] {m}")
        console.print(f"  [cyan]{len(presets) + 1}.[/] 手动输入")
        c = Prompt.ask("选择", default="1", console=console)
        if c.isdigit() and 1 <= int(c) <= len(presets):
            return presets[int(c) - 1]
        if c.isdigit() and int(c) == len(presets) + 1:
            return Prompt.ask("输入模型名", default=default, console=console)
    return Prompt.ask("输入模型名", default=default, console=console)


def edit_config(console: Console, cfg: UserConfig) -> UserConfig:
    """交互编辑配置 (/config 命令)."""
    console.print(f"当前 API base: [cyan]{cfg.api_base}[/]")
    cfg.api_base = Prompt.ask("新 API base", default=cfg.api_base, console=console)
    console.print(f"当前简单模型: [cyan]{cfg.simple_model}[/]")
    cfg.simple_model = Prompt.ask("新简单模型", default=cfg.simple_model, console=console)
    console.print(f"当前复杂模型: [cyan]{cfg.complex_model}[/]")
    cfg.complex_model = Prompt.ask("新复杂模型", default=cfg.complex_model, console=console)
    console.print(f"当前回退模型: [cyan]{cfg.fallback_model}[/]")
    cfg.fallback_model = Prompt.ask("新回退模型", default=cfg.fallback_model, console=console)
    save_config(cfg)
    console.print("[green]✓ 配置已更新[/]")
    return cfg
