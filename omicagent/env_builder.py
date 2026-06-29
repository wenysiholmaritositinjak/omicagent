"""能力2: 自动环境搭建与代码执行 (Env Builder).

读取数据集元信息 + 分析目的 -> 判断所需工具 (Seurat/Scanpy/hdWGCNA/SATURN/SAMap/SCENIC)
-> 生成 conda 环境配置 -> 在 WSL 本地 conda 执行安装/补包 -> 验证关键包可导入.
复用已有 conda env (scagent/seurat/samap), 避免重复建.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import config
from .llm_client import LLMClient
from .tool_dispatcher import ToolDispatcher, ShellResult

log = logging.getLogger("omicagent.env")


# ---- 工具 -> conda 包映射 ----
# tool: {language, conda_pkgs[(name,channel)], pip_pkgs, env(推荐复用), verify}
TOOL_PACKAGE_MAP = {
    "scanpy": {
        "language": "python", "env": "scagent",
        "conda": [("scanpy", "conda-forge"), ("anndata", "conda-forge"),
                  ("scikit-learn", "conda-forge"), ("scvi-tools", "conda-forge")],
        "pip": [],
        "verify_python": ["import scanpy", "import anndata"],
    },
    "seurat": {
        "language": "r", "env": "seurat",
        "conda": [("r-seurat", "conda-forge"), ("r-seuratobject", "conda-forge"),
                  ("r-dplyr", "conda-forge"), ("r-harmony", "conda-forge")],
        "pip": [],
        "verify_r": ["library(Seurat)"],
    },
    "hdwgcna": {
        "language": "r", "env": "seurat",
        "conda": [("r-hdwgcna", "conda-forge")],
        "pip": [],
        "verify_r": ["library(hdWGCNA)"],
    },
    "saturn": {
        "language": "python", "env": "scagent",
        "conda": [("pytorch", "conda-forge"), ("scvi-tools", "conda-forge"),
                  ("scikit-misc", "conda-forge"), ("biopython", "conda-forge")],
        "pip": ["fair-esm", "typed-argument-parser", "record-keeper", "plotly"],
        # scvi 裸导入因 torchvision/torch 算子注册冲突失败, 但 SATURN 训练可用(已验证);
        # 核心验证 torch, scvi 列为 optional
        "verify_python": ["import torch"],
        "verify_optional": ["import scvi"],
    },
    "samap": {
        "language": "python", "env": "samap",
        "conda": [("blast", "bioconda"), ("gxx", "conda-forge")],
        "pip": ["sc-samap"],
        "verify_python": ["from samap import SAMAP"],
    },
    "scenic": {
        "language": "python", "env": "scagent",
        "conda": [()],  # pyscenic 主要 pip
        "pip": ["pyscenic"],
        "verify_python": ["import pyscenic"],
    },
}


@dataclass
class EnvSpec:
    env_name: str
    language: str = "python"            # python / r
    analysis_tools: list[str] = field(default_factory=list)
    conda_packages: list[tuple] = field(default_factory=list)   # [(name, channel)]
    pip_packages: list[str] = field(default_factory=list)
    verify_cmds: list[str] = field(default_factory=list)
    verify_optional: list[str] = field(default_factory=list)  # 可选依赖, 失败不阻断
    exists: bool = False
    needs_install: list[str] = field(default_factory=list)   # 缺失包

    def to_dict(self) -> dict:
        d = asdict(self)
        d["conda_packages"] = [list(x) for x in self.conda_packages]
        return d


@dataclass
class EnvResult:
    spec: EnvSpec
    success: bool
    install_log: str = ""
    verify_log: str = ""
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"spec": self.spec.to_dict(), "success": self.success,
                "install_log": self.install_log[-2000:], "verify_log": self.verify_log[-2000:],
                "missing": self.missing}


class EnvBuilder:
    def __init__(self, llm: Optional[LLMClient] = None,
                 dispatcher: Optional[ToolDispatcher] = None):
        self.llm = llm or LLMClient()
        self.dispatcher = dispatcher or ToolDispatcher(self.llm)

    # ---------- 判断所需工具 ----------
    def analyze(self, metadata: dict, analysis_goal: str = "") -> EnvSpec:
        """根据数据集元信息 + 分析目的, 判断所需工具与语言, 生成 EnvSpec."""
        prompt = (
            "你是单细胞组学分析环境配置助手. 根据数据集元信息与分析目的, "
            "判断所需分析工具, 输出 JSON:\n"
            '{"analysis_tools":["scanpy"/"seurat"/"hdwgcna"/"saturn"/"samap"/"scenic" ...], '
            '"language":"python"/"r"/"both", "reason":"简述"}\n'
            "可用工具及其用途: scanpy/python质控聚类, seurat/R质控聚类, hdwgcna/R共表达, "
            "saturn/python跨物种整合(ESM), samap/python跨物种整合(BLAST), scenic/python调控网络.\n"
            f"数据集元信息: {metadata}\n分析目的: {analysis_goal or '(未指定, 默认标准分析)'}\n"
            "只输出 JSON."
        )
        data = self.llm.complete_json(prompt, task_type="complex", max_tokens=800)
        tools = data.get("analysis_tools", []) or []
        tools = [t.lower() for t in tools if t.lower() in TOOL_PACKAGE_MAP]
        if not tools:
            tools = ["scanpy"]  # 兜底

        # 合并各工具的包, 选主 env (python 优先 scagent, r 用 seurat, samap 用 samap)
        spec = self._build_spec(tools)
        log.info("环境分析: tools=%s env=%s exists=%s", tools, spec.env_name, spec.exists)
        return spec

    def _build_spec(self, tools: list[str]) -> EnvSpec:
        conda, pip, verify, verify_opt = [], [], [], []
        languages = set()
        for t in tools:
            m = TOOL_PACKAGE_MAP[t]
            languages.add(m["language"])
            conda.extend([x for x in m["conda"] if x])  # 跳过空 tuple
            pip.extend(m["pip"])
            verify.extend(m.get("verify_python", []) or m.get("verify_r", []))
            verify_opt.extend(m.get("verify_optional", []) or [])
        # env 选择: 若含 samap -> samap; 含 r 工具 -> seurat; 否则 scagent
        if "samap" in tools:
            env_name = "samap"
        elif "r" in languages:
            env_name = "seurat"
        else:
            env_name = "scagent"

        spec = EnvSpec(
            env_name=env_name,
            language="both" if len(languages) > 1 else (languages.pop() if languages else "python"),
            analysis_tools=tools,
            conda_packages=list(dict.fromkeys(conda)),  # 去重保序
            pip_packages=list(dict.fromkeys(pip)),
            verify_cmds=verify,
        )
        spec.verify_optional = verify_opt
        spec.exists = self._env_exists(env_name)
        return spec

    def _env_exists(self, env_name: str) -> bool:
        r = self.dispatcher.run_shell(f"conda env list 2>/dev/null | grep -q '^env' ; conda env list | awk '{{print $1}}' | grep -qx '{env_name}'")
        return r.success

    def _installed_pkgs(self, env_name: str) -> set[str]:
        r = self.dispatcher.run_shell(f"conda run -n {env_name} pip list 2>/dev/null; conda run -n {env_name} conda list 2>/dev/null")
        pkgs = set()
        for line in (r.stdout + "\n" + r.stderr).splitlines():
            parts = line.split()
            if parts:
                pkgs.add(parts[0].lower().replace("-", "_").replace("r-", ""))
        # 别名归一: torch/pytorch 互认 (conda 装 pytorch, pip 装 torch)
        if "torch" in pkgs:
            pkgs.add("pytorch")
        if "pytorch" in pkgs:
            pkgs.add("torch")
        return pkgs

    # ---------- 建环境 / 补包 ----------
    def build(self, spec: EnvSpec, reuse_existing: bool = True, timeout: int = 1800) -> EnvResult:
        """按 EnvSpec 安装环境; 已存在则只补缺失包. 返回验证结果."""
        install_log, verify_log = [], []
        missing = []

        if spec.exists and reuse_existing:
            log.info("复用已有 env: %s, 检查缺失包", spec.env_name)
            installed = self._installed_pkgs(spec.env_name)
            need_conda, need_pip = [], []
            for (name, ch) in spec.conda_packages:
                if name.lower().replace("-", "_") not in installed:
                    need_conda.append((name, ch))
                    missing.append(name)
            for name in spec.pip_packages:
                if name.lower().replace("-", "_") not in installed:
                    need_pip.append(name)
                    missing.append(name)
            spec.needs_install = [n for n, _ in need_conda] + need_pip
            if need_conda:
                cmd = self._conda_install_cmd(spec.env_name, need_conda)
                r = self.dispatcher.run_shell(cmd, timeout=timeout)
                install_log.append(f"$ {cmd}\n{r.stdout[-1000:]}\n{r.stderr[-500:]}")
            if need_pip:
                cmd = f"conda run -n {spec.env_name} pip install {' '.join(need_pip)}"
                r = self.dispatcher.run_shell(cmd, timeout=timeout)
                install_log.append(f"$ {cmd}\n{r.stdout[-1000:]}\n{r.stderr[-500:]}")
        else:
            # 新建 env
            cmd = (f"conda create -n {spec.env_name} -y "
                   f"-c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge "
                   f"-c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/bioconda "
                   f"python=3.10")
            r = self.dispatcher.run_shell(cmd, timeout=timeout)
            install_log.append(f"$ {cmd}\n{r.stdout[-800:]}\n{r.stderr[-400:]}")
            if spec.conda_packages:
                cmd = self._conda_install_cmd(spec.env_name, spec.conda_packages)
                r = self.dispatcher.run_shell(cmd, timeout=timeout)
                install_log.append(f"$ {cmd}\n{r.stdout[-1000:]}\n{r.stderr[-500:]}")
            if spec.pip_packages:
                cmd = f"conda run -n {spec.env_name} pip install -i https://pypi.tuna.tsinghua.edu.cn/simple {' '.join(spec.pip_packages)}"
                r = self.dispatcher.run_shell(cmd, timeout=timeout)
                install_log.append(f"$ {cmd}\n{r.stdout[-1000:]}\n{r.stderr[-500:]}")

        # 验证: 核心命令(失败阻断) + 可选命令(失败仅警告)
        ok_all = True
        for vcmd in spec.verify_cmds + spec.verify_optional:
            is_optional = vcmd in spec.verify_optional
            # python: "import X" 或 "from X import Y"; R: "library(X)"
            if vcmd.startswith("import") or vcmd.startswith("from "):
                full = f"conda run -n {spec.env_name} python -c \"{vcmd}\""
            else:  # library(...)
                full = f"conda run -n {spec.env_name} Rscript -e '{vcmd}'"
            r = self.dispatcher.run_shell(full, timeout=120)
            tag = "[optional]" if is_optional else "[core]"
            verify_log.append(f"$ {full} {tag}\n[{r.returncode}] {r.stdout[-300:]} {r.stderr[-300:]}")
            if not r.success and not is_optional:
                ok_all = False
            elif not r.success and is_optional:
                log.warning("可选依赖验证失败(不阻断): %s", vcmd)

        # 环境快照
        snap = self.dispatcher.run_shell(f"conda env export -n {spec.env_name} > {config.RESULTS_DIR}/{spec.env_name}_env.yml 2>/dev/null")
        return EnvResult(spec=spec, success=ok_all,
                         install_log="\n".join(install_log), verify_log="\n".join(verify_log),
                         missing=missing)

    def _conda_install_cmd(self, env: str, pkgs: list[tuple]) -> str:
        names = " ".join(n for n, _ in pkgs)
        return (f"conda install -n {env} -y "
                f"-c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge "
                f"-c https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/bioconda {names}")

    # ---------- 便捷: 按工具直接返回已建 env ----------
    def ensure_env_for_tool(self, tool: str) -> EnvSpec:
        tool = tool.lower()
        if tool not in TOOL_PACKAGE_MAP:
            raise ValueError(f"未知工具: {tool}, 可选: {list(TOOL_PACKAGE_MAP)}")
        return self._build_spec([tool])

    # ---------- 按配方构建 (固化经验, 避坑) ----------
    def build_with_recipe(self, recipe_name: str, env_name: str = "",
                          timeout: int = 3600) -> dict:
        """按固化配方构建环境, 避开已知坑. 返回 {recipe, steps_run, pitfalls, verify}.

        recipe_name: seurat4 / seurat5 / samap / saturn / scanpy
        env_name: 自定义 env 名 (默认用配方名)
        """
        from .env_recipes import get_recipe
        recipe = get_recipe(recipe_name)
        env = env_name or recipe.name
        log.info("按配方构建: %s (env=%s)", recipe.name, env)

        # 1. 打印避坑提示 (重要: 让用户知道为什么这么装)
        pitfalls_text = "\n".join(f"  ⚠ {p}" for p in recipe.pitfalls)

        # 2. 执行构建步骤
        steps_run = []
        for i, step in enumerate(recipe.steps, 1):
            # R 命令 vs shell 命令
            if step.startswith("install.packages") or step.startswith("BiocManager") or \
               step.startswith("remotes::") or step.startswith("remove.packages"):
                # R 命令: 用 Rscript -e 执行 (在指定 R env 里, 默认 seurat)
                r_env = "seurat" if recipe.language == "r" else env
                cmd = f"conda run -n {r_env} Rscript -e '{step}'" if _env_exists(r_env) else f"Rscript -e '{step}'"
            elif step.startswith("conda "):
                cmd = step  # conda activate 不在子进程生效, 跳过 activate 行
                if "activate" in step:
                    continue
            elif step.startswith("pip "):
                cmd = f"conda run -n {env} {step}" if _env_exists(env) else step
            else:
                cmd = step
            log.info("[配方 %d] %s", i, cmd)
            r = self.dispatcher.run_shell(cmd, timeout=timeout)
            steps_run.append({"step": i, "cmd": cmd, "success": r.success,
                              "tail": (r.stdout + r.stderr)[-400:]})
            if not r.success:
                log.warning("步骤 %d 失败, 继续尝试后续 (可能部分依赖已满足)", i)

        # 3. 验证
        verify_results = []
        for v in recipe.verify:
            if recipe.language == "r":
                r_env = "seurat" if _env_exists("seurat") else env
                cmd = f"conda run -n {r_env} Rscript -e 'cat({v}, \"\\n\")'" if _env_exists(r_env) else f"Rscript -e 'cat({v})'"
            else:
                cmd = f"conda run -n {env} {v}" if _env_exists(env) else v
            r = self.dispatcher.run_shell(cmd, timeout=120)
            verify_results.append({"check": v, "output": r.stdout.strip()[-100:], "success": r.success})

        return {
            "recipe": recipe.name, "env": env, "desc": recipe.desc,
            "versions": recipe.versions, "steps_run": steps_run,
            "verify": verify_results,
            "pitfalls": recipe.pitfalls, "pitfalls_text": pitfalls_text,
            "notes": recipe.notes,
        }


def _env_exists(name: str) -> bool:
    """检查 conda env 是否存在."""
    import subprocess
    r = subprocess.run(f"conda env list 2>/dev/null | awk '{{print $1}}' | grep -qx '{name}'",
                       shell=True, capture_output=True)
    return r.returncode == 0


def list_envs() -> list[dict]:
    """列出所有 conda 环境 + 检测关键能力 (已有环境记忆, 优先复用).

    返回 [{name, language, tools, has_seurat, has_scanpy, has_samap, has_saturn}]
    """
    import subprocess
    r = subprocess.run("conda env list 2>/dev/null | grep -v '^#' | awk '{print $1}'",
                       shell=True, capture_output=True, text=True)
    envs = [e for e in r.stdout.split() if e and e != "base"]
    out = []
    for env in envs:
        # 检测每个 env 的关键包
        caps = _detect_env_caps(env)
        out.append({"name": env, **caps})
    return out


def _detect_env_caps(env: str) -> dict:
    """检测某 conda env 的能力 (含哪些关键包)."""
    import subprocess
    caps = {"language": "", "has_seurat": False, "has_scanpy": False,
            "has_samap": False, "has_saturn": False, "tools": []}
    # Python 能力
    r = subprocess.run(f'conda run -n {env} python -c "import scanpy" 2>/dev/null',
                       shell=True, capture_output=True)
    if r.returncode == 0:
        caps["has_scanpy"] = True; caps["language"] = "python"; caps["tools"].append("scanpy")
    r = subprocess.run(f'conda run -n {env} python -c "import torch" 2>/dev/null',
                       shell=True, capture_output=True)
    if r.returncode == 0:
        caps["has_saturn"] = True; caps["tools"].append("saturn")
    r = subprocess.run(f'conda run -n {env} python -c "from samap import SAMAP" 2>/dev/null',
                       shell=True, capture_output=True)
    if r.returncode == 0:
        caps["has_samap"] = True; caps["language"] = "python"; caps["tools"].append("samap")
    # R 能力
    r = subprocess.run(f'conda run -n {env} Rscript -e "library(Seurat)" 2>/dev/null',
                       shell=True, capture_output=True)
    if r.returncode == 0:
        caps["has_seurat"] = True; caps["language"] = "r"; caps["tools"].append("seurat")
    return caps


def find_existing_env(language: str = "", tool: str = "") -> dict | None:
    """检索已有 conda 环境, 找到能处理指定语言/工具的环境 (记忆复用).

    language: 'r' / 'python'; tool: 'seurat' / 'scanpy' / 'samap' / 'saturn'
    返回 {name, ...} 或 None (无则需构建).
    """
    envs = list_envs()
    for env in envs:
        if tool == "seurat" and env.get("has_seurat"):
            return env
        if tool == "scanpy" and env.get("has_scanpy"):
            return env
        if tool == "samap" and env.get("has_samap"):
            return env
        if tool == "saturn" and env.get("has_saturn"):
            return env
    # 按 language 兜底
    if language:
        for env in envs:
            if env.get("language") == language:
                return env
    return None
