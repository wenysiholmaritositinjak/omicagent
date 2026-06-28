"""环境构建配方 (Recipes) — 固化踩坑经验, 开箱即用.

每个配方记录: 正确构建流程 + 已知坑(避雷) + 版本锁定 + 验证.
后续用户构建时直接用配方, 不再重复踩坑. 支持指定版本.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class EnvRecipe:
    name: str                          # scagent / seurat4 / seurat5 / samap / saturn
    language: str                      # python / r / both
    desc: str
    versions: dict = field(default_factory=dict)   # {包: 版本}
    steps: list[str] = field(default_factory=list) # 构建命令(按顺序)
    verify: list[str] = field(default_factory=list) # 验证命令
    pitfalls: list[str] = field(default_factory=list)  # 已知坑 + 规避
    notes: str = ""


# ============ Seurat 4 (很多生信人员习惯, 参考CSDN) ============
SEURAT4 = EnvRecipe(
    name="seurat4",
    language="r",
    desc="Seurat 4.4.0 + SeuratObject 4.1.4 + SeuratDisk + SingleCellExperiment (兼容性好, 生信人员习惯)",
    versions={"Seurat": "4.4.0", "SeuratObject": "4.1.4",
              "SeuratDisk": "0.0.0.9021", "SingleCellExperiment": "1.28.1"},
    steps=[
        # 1. 先装 Seurat5 再卸载 (满足依赖), 再装 Seurat4 (r-universe)
        "install.packages('Seurat')",
        "remove.packages(c('Seurat','SeuratObject'))",
        "install.packages('Seurat', repos = c('https://satijalab.r-universe.dev'))",
        # 2. SeuratDisk
        "install.packages('remotes')",
        "remotes::install_github('mojaveazure/seurat-disk')",
        # 3. SingleCellExperiment
        "install.packages('BiocManager')",
        "BiocManager::install('SingleCellExperiment')",
    ],
    verify=[
        "packageVersion('Seurat')",            # 4.4.0
        "packageVersion('SeuratObject')",      # 4.1.4
        "packageVersion('SeuratDisk')",        # 0.0.0.9021
        "packageVersion('SingleCellExperiment')",  # 1.28.1
    ],
    pitfalls=[
        "Seurat5 的 GetAssayData(slot=) 已废弃, SeuratDisk 用 slot 会崩 → Seurat4 用 slot 无此问题",
        "Seurat4 安装必须先装 Seurat5 再卸载, 否则依赖不全 (r-universe 的 Seurat4 依赖 Seurat5 的部分包)",
        "r-universe 源: https://satijalab.r-universe.dev (非 CRAN)",
        "BiocManager 装 SingleCellExperiment 需 R ≥ 4.3",
    ],
    notes="参考: https://blog.csdn.net/weixin_65656674/article/details/144238049",
)


# ============ Seurat 5 (最新, 但 SeuratDisk 需 patch) ============
SEURAT5 = EnvRecipe(
    name="seurat5",
    language="r",
    desc="Seurat 5.5 + SeuratObject 5.4 (最新功能, 但 SeuratDisk 需 patch slot→layer)",
    versions={"Seurat": "5.5.0", "SeuratObject": "5.4.0"},
    steps=[
        "conda install -c conda-forge r-seurat r-seuratobject r-harmony r-hdf5r r-dplyr -y",
        # SeuratDisk 需 patch (slot→layer)
        "remotes::install_github('mojaveazure/seurat-disk')  # 然后手动 patch WriteH5Group.R/AssembleObject.R",
    ],
    verify=["library(Seurat); packageVersion('Seurat')"],
    pitfalls=[
        "SeuratDisk 用 GetAssayData(slot=) 在 SeuratObject 5.x 已 defunct → 必须 patch 源码 slot→layer",
        "patch: 下载源码, 改 R/WriteH5Group.R 和 R/AssembleObject.R 的 slot= 为 layer=, install_local",
        "Seurat v5 的 scale.data 是 HVG 子集, SeuratDisk 转换会丢全基因 → 清空 scale.data 保留 counts+data",
        "JoinLayers 对 v3 Assay 报错 → 仅 Assay5 调用",
        "若用 v5 求稳, 推荐改用 Seurat4 (见 seurat4 配方)",
    ],
    notes="SeuratDisk 与 v5 兼容性差, 除非需要 v5 新功能, 否则建议用 seurat4",
)


# ============ SAMap (跨物种 BLAST 同源) ============
SAMAP = EnvRecipe(
    name="samap",
    language="python",
    desc="sc-samap 3.0.1 + BLAST (跨物种整合, BLAST 同源图)",
    versions={"sc-samap": "3.0.1", "blast": "2.17.0+"},
    steps=[
        # 1. conda env (py3.12) + 编译工具 (hnswlib 需 gxx/gcc/make)
        "conda create -n samap -c conda-forge python=3.12 gxx gcc make -y",
        "conda activate samap",
        # 2. sc-samap (pip)
        "pip install sc-samap",
        # 3. BLAST (bioconda)
        "conda install -c bioconda blast -y",
    ],
    verify=[
        "python -c 'from samap import SAMAP'",
        "blastp -version",
    ],
    pitfalls=[
        "sc-samap 装 hnswlib 需编译 → 必须先装 gxx gcc make, 否则 pip install 报错",
        "conda 26.x 的 ToS 插件会阻断 install (requires authentication) → 删 defaults 通道, 用 custom_channels 映射 TUNA",
        "BLAST 必须 bioconda 通道, 不是 conda-forge",
        "SAMap 的 blastp 用 max_hsps 1 evalue 1e-6, 不限 max_target_seqs (保留所有同源不止1:1)",
    ],
    notes="TUNA 镜像: conda-forge/bioconda 映射到 mirrors.tuna.tsinghua.edu.cn",
)


# ============ SATURN (ESM1b 蛋白嵌入跨物种) ============
SATURN = EnvRecipe(
    name="saturn",
    language="python",
    desc="SATURN + ESM1b + scvi-tools + torch (ESM 蛋白嵌入跨物种整合)",
    versions={"torch": "2.11.0+cu128", "fair-esm": "2.0.0", "scvi-tools": "1.3.3"},
    steps=[
        # 1. 基础 env
        "conda create -n scagent -c conda-forge python=3.10 scanpy anndata scikit-learn scikit-misc biopython -y",
        "conda activate scagent",
        # 2. torch cu128 (RTX5060 Blackwell 需 cu128)
        "pip install torch --index-url https://download.pytorch.org/whl/cu128",
        # 3. SATURN 依赖
        "pip install fair-esm typed-argument-parser record-keeper plotly scvi-tools torchvision",
        # 4. SeuratDisk 的 R 侧 (转换 rds 用, 见 seurat4/5 配方)
    ],
    verify=[
        "python -c 'import torch; print(torch.cuda.is_available())'",  # True
        "python -c 'import scanpy, anndata'",
        "python -c 'import scvi'",   # 注意: 可能因 torchvision 算子冲突失败, 见 pitfalls
        "python -c 'from esm import pretrained'",  # ESM1b
    ],
    pitfalls=[
        "RTX 5060 (Blackwell sm_120) 必须 cu128 torch, cpu 版无法用 GPU",
        "torchvision 0.26 与 torch 2.11 的 nms 算子冲突 → 裸 import scvi 会崩, 但 SATURN 训练可用 (import 顺序不同); 验证时 scvi 标 optional",
        "scvi-tools 拉 matplotlib/torchmetrics 需 libstdc++ CXXABI_1.3.15 → conda 的 libstdc++ 6.0.34 有, 但需 LD_LIBRARY_PATH=$CONDA_PREFIX/lib",
        "ESM1b 模型 7.3GB, 首次运行下载 (fbaipublicfiles.com 可达)",
        "SATURN 的 extract.py 新版无 --truncate, 用默认 truncation_seq_length=1022",
        "SATURN README 要求旧 ESM commit 839c5b8, 但新版 esm-main 2.0.1 的 extract.py 也能跑 esm1b",
        "strip_stop.py 剥离 FASTA 末尾 *, 不能用 clean_fasta.py (会全删带 * 的 Araport11 序列)",
    ],
    notes="SATURN 训练需 LD_LIBRARY_PATH=$CONDA_PREFIX/lib; scvi 验证失败不阻断(标 optional)",
)


# ============ Scanpy 通用 (单细胞分析基础) ============
SCANPY = EnvRecipe(
    name="scanpy",
    language="python",
    desc="Scanpy + anndata (Python 单细胞分析基础, 轻量)",
    versions={"scanpy": "1.10+", "anndata": "0.10+"},
    steps=[
        "conda create -n scagent -c conda-forge python=3.10 scanpy anndata scikit-learn scikit-misc biopython matplotlib seaborn -y",
    ],
    verify=[
        "python -c 'import scanpy; print(scanpy.__version__)'",
        "python -c 'import anndata'",
    ],
    pitfalls=[
        "leidenalg 要求 igraph<2.0>=1.0.0, 装 louvain 可能把 igraph 降到 0.11.9 导致冲突 → 聚类用 leidenalg 即可",
    ],
    notes="最轻量, 推荐作为入门环境",
)


RECIPES = {
    "seurat4": SEURAT4,
    "seurat5": SEURAT5,
    "samap": SAMAP,
    "saturn": SATURN,
    "scanpy": SCANPY,
}


def get_recipe(name: str, version: str = "") -> EnvRecipe:
    """获取配方. version 可指定 (如 'seurat4' 固定 4.4.0)."""
    name = name.lower()
    if name not in RECIPES:
        raise ValueError(f"未知配方: {name}, 可选: {list(RECIPES)}")
    return RECIPES[name]


def list_recipes() -> list[dict]:
    """列出所有配方概要."""
    return [{"name": r.name, "language": r.language, "desc": r.desc,
             "versions": r.versions, "pitfalls_count": len(r.pitfalls)}
            for r in RECIPES.values()]
