"""OmicAgent 全局配置.

配置优先级: 环境变量 > ~/.omicagent/config.toml (用户配置) > 项目根 .env > 默认值.
默认值对应 DCS Cloud 统一 API (已验证 OpenAI 兼容).
"""
from __future__ import annotations
import os
from pathlib import Path

# 1. 先读用户配置 (~/.omicagent/config.toml), 注入环境变量 (优先级最低, 不覆盖已有)
def _load_user_config():
    try:
        import toml
        cfg_path = Path.home() / ".omicagent" / "config.toml"
        if cfg_path.exists():
            data = toml.load(cfg_path)
            api = data.get("api", {})
            models = data.get("models", {})
            paths = data.get("paths", {})
            os.environ.setdefault("OMICAGENT_API_BASE", api.get("base", ""))
            os.environ.setdefault("OMICAGENT_API_KEY", api.get("key", ""))
            os.environ.setdefault("OMICAGENT_SIMPLE_MODEL", models.get("simple", "deepseek-v4-pro"))
            os.environ.setdefault("OMICAGENT_COMPLEX_MODEL", models.get("complex", "glm-5.2"))
            os.environ.setdefault("OMICAGENT_COMPLEX_FALLBACK", models.get("fallback", "claude-opus-4-8"))
            if paths.get("data_dir"):
                os.environ.setdefault("OMICAGENT_DATA_DIR", paths["data_dir"])
    except Exception:
        pass

_load_user_config()

# 2. 项目根 .env (开发时用)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    _env = Path(__file__).resolve().parent.parent / ".env"
    if _env.exists():
        for _line in _env.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ---- DCS Cloud 统一 API ----
API_BASE = os.environ.get(
    "OMICAGENT_API_BASE",
    "https://dcsapi.dcs.cloud/api/aigress/unified/v1",
)
API_KEY = os.environ.get("OMICAGENT_API_KEY", "")
CHAT_ENDPOINT = API_BASE.rstrip("/") + "/chat/completions"
MODELS_ENDPOINT = API_BASE.rstrip("/") + "/models"

# ---- 模型路由 (按用户要求) ----
# 简单任务: 代码生成 / 报告摘要 / 元数据解析
SIMPLE_MODEL = os.environ.get("OMICAGENT_SIMPLE_MODEL", "deepseek-v4-pro")
# 复杂任务: 任务规划 / 注释映射 / 推理, 主用 glm-5.2, 失败回退 claude-opus-4-8
COMPLEX_MODEL = os.environ.get("OMICAGENT_COMPLEX_MODEL", "glm-5.2")
COMPLEX_FALLBACK = os.environ.get("OMICAGENT_COMPLEX_FALLBACK", "claude-opus-4-8")

# ---- 调用参数 ----
REQUEST_TIMEOUT = int(os.environ.get("OMICAGENT_TIMEOUT", "180"))
MAX_RETRIES = int(os.environ.get("OMICAGENT_MAX_RETRIES", "3"))

# 默认 max_tokens (推理模型需要较大值: 思考+答案)
MAX_TOKENS_SIMPLE = int(os.environ.get("OMICAGENT_MAX_TOKENS_SIMPLE", "4096"))
MAX_TOKENS_COMPLEX = int(os.environ.get("OMICAGENT_MAX_TOKENS_COMPLEX", "6144"))

# ---- OmicSeek 数据检索 (占位, 实际接口地址待平台提供) ----
OMICSEEK_BASE = os.environ.get("OMICSEEK_BASE", "https://omicseek.cngb.org")

# ---- NCBI E-utilities (能力1 数据检索主通道 + 能力3 文献上下文) ----
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")  # 可选, 有 key 提速
GEO_FTP_BASE = "https://ftp.ncbi.nlm.nih.gov/geo/series"

# ---- 路径 ----
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "omicagent" / "templates"
DATA_DIR = Path(os.environ.get("OMICAGENT_DATA_DIR", PROJECT_ROOT.parent / "data"))
RESULTS_DIR = Path(os.environ.get("OMICAGENT_RESULTS_DIR", PROJECT_ROOT / "results"))
LOG_DIR = PROJECT_ROOT / "logs"

for _d in (DATA_DIR, RESULTS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def model_chain(task_type: str) -> list[str]:
    """返回某类任务应依次尝试的模型列表."""
    if task_type == "complex":
        return [COMPLEX_MODEL, COMPLEX_FALLBACK]
    return [SIMPLE_MODEL]
