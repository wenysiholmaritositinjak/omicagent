"""跨物种整合封装 (能力4).

把已验证的 SAMap / SATURN 流程封装为可对话调用的函数:
- method=samap: BLAST 同源图 + SAMAP 联合嵌入 (Python API)
- method=saturn: ESM1b 基因嵌入 + SATURN macrogene 训练 (subprocess 调 train-saturn.py)

前置 (假定已就绪, 缺失则报错提示):
- samap: blastp maps 目录 (os_to_at.txt / at_to_os.txt), conda env 'samap'
- saturn: ESM1b 基因嵌入 .pt, conda env 'scagent', SATURN 仓库
"""
from __future__ import annotations
import os, logging, subprocess, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("omicagent.cross_species")

# 默认路径 (可被环境变量覆盖)
DEFAULT_MAPS_DIR = os.environ.get("OMICAGENT_MAPS_DIR", str(Path.home() / "bioinfo" / "data" / "maps"))
DEFAULT_SATURN_REPO = os.environ.get("OMICAGENT_SATURN_REPO", "")
DEFAULT_SATURN_IN_DATA = os.environ.get("OMICAGENT_SATURN_IN_DATA", "")


@dataclass
class CrossSpeciesResult:
    success: bool
    method: str
    h5ad: str = ""
    umap_pdfs: list[str] = field(default_factory=list)
    alignment_score: float = 0.0
    mapping_csv: str = ""
    elapsed: float = 0.0
    error: str = ""
    log: str = ""

    def summary(self) -> str:
        if not self.success:
            return f"跨物种整合失败 ({self.method}): {self.error}"
        s = f"跨物种整合完成 ({self.method}, {self.elapsed:.0f}s)\n"
        s += f"  对齐分数: {self.alignment_score:.3f}\n"
        s += f"  整合 h5ad: {self.h5ad}\n"
        s += f"  UMAP PDF: {', '.join(Path(p).name for p in self.umap_pdfs)}"
        if self.mapping_csv:
            s += f"\n  映射表: {self.mapping_csv}"
        return s


def run_cross_species(data1: str, data2: str, method: str = "samap",
                      maps_dir: Optional[str] = None, workdir: Optional[str] = None,
                      species_keys: tuple = ("os", "at")) -> CrossSpeciesResult:
    """跨物种整合入口.

    data1/data2: 两物种 h5ad (X=counts, obs 含 celltype)
    method: 'samap' (BLAST 同源) 或 'saturn' (ESM 蛋白嵌入)
    maps_dir: blastp maps 目录 (samap 用), 默认 DEFAULT_MAPS_DIR
    """
    method = method.lower()
    t0 = time.time()
    if method == "samap":
        r = _run_samap(data1, data2, maps_dir or DEFAULT_MAPS_DIR, workdir, species_keys)
    elif method == "saturn":
        r = _run_saturn(data1, data2, workdir)
    else:
        return CrossSpeciesResult(success=False, method=method, error=f"未知方法: {method}")
    r.elapsed = time.time() - t0
    return r


def _run_samap(data1, data2, maps_dir, workdir, species_keys) -> CrossSpeciesResult:
    """SAMap 整合 (Python API)."""
    try:
        from samap import SAMAP, get_mapping_scores
    except ImportError:
        return CrossSpeciesResult(success=False, method="samap",
                                  error="未安装 sc-samap, 请: conda activate samap 或 pip install sc-samap")
    import scanpy as sc
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd, numpy as np

    k1, k2 = species_keys
    workdir = Path(workdir or Path.home() / "bioinfo" / "data" / "samap_out_cli")
    workdir.mkdir(parents=True, exist_ok=True)

    # 检查 maps
    map_subdir = Path(maps_dir) / f"{k1}{k2}"
    if not (map_subdir / f"{k1}_to_{k2}.txt").exists():
        return CrossSpeciesResult(success=False, method="samap",
                                  error=f"blastp maps 缺失: {map_subdir}/{k1}_to_{k2}.txt (先运行 samap blastp)")

    log.info("SAMap 整合: %s + %s, maps=%s", data1, data2, map_subdir)
    try:
        sm = SAMAP(sams={k1: data1, k2: data2}, f_maps=str(Path(maps_dir) / ""))
        sm.run(hrs=0.5, run_smaps=False)
        adata = sm.merge_sams()
        sc.pp.neighbors(adata, n_neighbors=15, use_rep="X")
        sc.tl.umap(adata)
        adata.write_h5ad(str(workdir / "samap_integrated.h5ad"))

        # 对齐分数
        score = 0.0
        try:
            sc_df = sm.sc[1]
            score = float(sc_df.iloc[-1, -1]) if sc_df.size else 0.0
        except Exception:
            pass

        # 出图 (内联: 灰底+图例过滤+并排 PDF)
        pdfs = _plot_umaps(adata, workdir, "samap_cli", species_keys)

        # 映射分数表
        mapping_csv = ""
        try:
            D, A = get_mapping_scores(sm, keys={k1: "celltype", k2: "celltype"}, n_top=0)
            D.to_csv(str(workdir / "celltype_mapping.csv"))
            mapping_csv = str(workdir / "celltype_mapping.csv")
        except Exception as e:
            log.warning("映射分数计算失败: %s", e)

        return CrossSpeciesResult(success=True, method="samap",
                                  h5ad=str(workdir / "samap_integrated.h5ad"),
                                  umap_pdfs=pdfs, alignment_score=score,
                                  mapping_csv=mapping_csv)
    except Exception as e:
        log.exception("SAMap 失败")
        return CrossSpeciesResult(success=False, method="samap", error=str(e))


def _run_saturn(data1, data2, workdir) -> CrossSpeciesResult:
    """SATURN 训练 (subprocess 调 train-saturn.py)."""
    saturn_repo = DEFAULT_SATURN_REPO
    if not saturn_repo or not Path(saturn_repo).exists():
        return CrossSpeciesResult(success=False, method="saturn",
                                  error="SATURN 仓库路径未配置 (设 OMICAGENT_SATURN_REPO)")
    in_data = DEFAULT_SATURN_IN_DATA or workdir
    workdir = Path(workdir or Path.home() / "bioinfo" / "data" / "saturn_out_cli")
    workdir.mkdir(parents=True, exist_ok=True)

    in_csv = workdir / "in_data.csv"
    in_csv.write_text(
        f"species,path,embedding_path\n"
        f"os,{data1},{Path.home()}/bioinfo/data/esm/Rice/Rice.gene_symbol_to_embedding_ESM1b.pt\n"
        f"at,{data2},{Path.home()}/bioinfo/data/esm/Arabidopsis/At.gene_symbol_to_embedding_ESM1b.pt\n",
        encoding="utf-8")

    cmd = (f"conda run -n scagent bash -c 'cd {saturn_repo} && "
           f"export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH && "
           f"python train-saturn.py --in_data {in_csv} --in_label_col celltype --ref_label_col tissue "
           f"--embedding_model ESM1b --device cuda --work_dir {workdir}/ --log_dir tboard_log/ "
           f"--hv_genes 2000 --num_macrogenes 1000 --pretrain_epochs 100 --epochs 50 --seed 0'")
    log.info("SATURN 训练: %s", cmd)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3600, executable="/bin/bash")
        if r.returncode != 0:
            return CrossSpeciesResult(success=False, method="saturn",
                                      error=r.stderr[-800:] or r.stdout[-800:], log=r.stdout[-2000:])
        # 找最终 h5ad
        results_dir = workdir / "saturn_results"
        h5ads = sorted(results_dir.glob("*.h5ad"))
        h5ads = [h for h in h5ads if "_pretrain" not in h.name and "_ep_" not in h.name]
        if not h5ads:
            return CrossSpeciesResult(success=False, method="saturn", error="未找到最终 h5ad", log=r.stdout[-2000:])
        # 出图 (调 fix_saturn_plot 或内联)
        pdfs = _saturn_plot(h5ads[0], workdir)
        return CrossSpeciesResult(success=True, method="saturn", h5ad=str(h5ads[0]),
                                  umap_pdfs=pdfs, log=r.stdout[-1500:])
    except Exception as e:
        return CrossSpeciesResult(success=False, method="saturn", error=str(e))


def _plot_umaps(adata, outdir, tag, species_keys) -> list[str]:
    """内联出图: 分物种灰底+图例过滤+并排 PDF (复用 plot_cross_species 规范)."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    outdir = Path(outdir)
    if "X_umap" not in adata.obsm:
        return []
    xy = adata.obsm["X_umap"]
    sp_col = "species" if "species" in adata.obs else None
    if sp_col is None:
        return []
    sp = adata.obs[sp_col].astype(str).values
    # 统一 celltype
    ct = None
    for c in ["celltype", "ct_unified", "labels2"]:
        if c in adata.obs:
            ct = adata.obs[c].astype(str).values; break
    if ct is None:
        return []
    species_list = sorted(set(sp) - {"nan"})
    cats = sorted([c for c in set(ct) if c not in ("na", "nan", "Unassigned", "unassigned")])
    cmap = plt.get_cmap("tab20")
    palette = {c: cmap(i % 20) for i, c in enumerate(cats)}

    def plot_panel(ax, focal=None, title=""):
        if focal is None:
            order = np.arange(len(sp))
        else:
            order = [i for i in range(len(sp)) if sp[i] != focal] + [i for i in range(len(sp)) if sp[i] == focal]
        for i in order:
            if focal is not None and sp[i] != focal:
                ax.scatter(xy[i, 0], xy[i, 1], c="#DDDDDD", s=5, alpha=0.5, zorder=1, edgecolors="none")
            else:
                ax.scatter(xy[i, 0], xy[i, 1], c=[palette[ct[i]]], s=8, alpha=0.8, zorder=2, edgecolors="none")
        ax.set_title(title); ax.set_xticks([]); ax.set_yticks([])

    def legend(ax, focal=None):
        ci = sorted([c for c in set(ct[sp == focal]) if c not in ("na", "nan", "Unassigned")]) if focal else cats
        h = [Line2D([0], [0], marker="o", color="w", markerfacecolor=palette[c], markersize=6, label=c) for c in ci]
        ax.legend(handles=h, bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7, frameon=False)

    pdfs = []
    # 并排
    n = len(species_list)
    fig, axes = plt.subplots(1, n, figsize=(7 * n, 6), squeeze=False)
    for j, s in enumerate(species_list):
        plot_panel(axes[0, j], focal=s, title=f"{s} (others gray)")
        legend(axes[0, j], focal=s)
    plt.tight_layout()
    p = outdir / f"{tag}_umap_species_sidebyside.pdf"
    fig.savefig(p, bbox_inches="tight"); plt.close(); pdfs.append(str(p))
    return pdfs


def _saturn_plot(h5ad_path, workdir) -> list[str]:
    """SATURN 出图: 修复 labels2 + UMAP + PDF."""
    try:
        import scanpy as sc
        import pandas as pd
        a = sc.read_h5ad(str(h5ad_path))
        # 修复 labels2 (SATURN split('_')[-1] 破坏下划线名)
        if "labels" in a.obs and "species" in a.obs:
            fixed = []
            for lab, s in zip(a.obs["labels"].astype(str), a.obs["species"].astype(str)):
                pfx = s + "_"
                fixed.append(lab[len(pfx):] if lab.startswith(pfx) else lab)
            a.obs["labels2"] = pd.Categorical(fixed)
        sc.pp.neighbors(a, n_neighbors=15, use_rep="X")
        sc.tl.umap(a)
        return _plot_umaps(a, workdir, "saturn_cli", ("os", "at"))
    except Exception as e:
        log.warning("SATURN 出图失败: %s", e)
        return []
