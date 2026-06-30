"""本地数据集目录 (scPlantDB + PlantScRNAdb 等已知数据集, 含 h5ad/rds 下载).

检索时优先匹配本地目录(快, 已知数据), 再网络搜索补充.
"""
from __future__ import annotations
import json, logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("omicagent.local_datasets")

DATA_DIR = Path(__file__).resolve().parent / "data"
# 多个本地数据源
DATA_FILES = [
    DATA_DIR / "scplantdb_datasets.json",       # scPlantDB 67 数据集
    DATA_DIR / "plantscrnadb_datasets.json",    # PlantScRNAdb 106 数据集
]


def load_local_datasets() -> list[dict]:
    """加载所有本地数据集目录 (合并多源)."""
    all_ds = []
    for f in DATA_FILES:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            all_ds.extend(data.get("datasets", []))
        except Exception as e:
            log.warning("读 %s 失败: %s", f.name, e)
    return all_ds


def search_local_datasets(species: str = "", tissue: str = "",
                          keyword: str = "", topk: int = 10) -> list[dict]:
    """在本地目录按物种/组织/关键词匹配, 返回优先级排序结果.

    本地数据已含 h5ad/rds 格式信息, 命中即直接可下载, 无需网络检索.
    """
    ds = load_local_datasets()
    if not ds:
        return []
    sp = (species or "").lower()
    ts = (tissue or "").lower()
    kw = (keyword or "").lower()
    out = []
    for d in ds:
        score = 0
        d_sp = (d.get("species") or "").lower()
        # tissue 字段统一: scPlantDB 用 tissue, PlantScRNAdb 用 tissues
        d_ts = (d.get("tissue") or d.get("tissues") or "").lower()
        # 物种匹配 (拉丁名部分匹配, 如 arabidopsis 命中 Arabidopsis thaliana)
        if sp and (sp in d_sp or d_sp in sp):
            score += 10
        # 组织匹配 (部分, 如 root 命中 Root tip/Whole root)
        if ts and (ts in d_ts or d_ts in ts):
            score += 8
        # 关键词匹配 (拆词, 任一命中加分; 搜 publication/tissue/species/title)
        if kw:
            text = (d.get("publication", "") + " " + d.get("tissue", "") + " " +
                    d.get("tissues", "") + " " + d.get("species", "") + " " +
                    d.get("title", "")).lower()
            for w in kw.split():
                if len(w) > 2 and w in text:
                    score += 3
        if score > 0 or not (sp or ts or kw):
            # 归一化输出字段 (统一 tissue)
            entry = {**d}
            if "tissue" not in entry and "tissues" in entry:
                entry["tissue"] = entry["tissues"]
            entry["_match_score"] = score
            out.append(entry)
    out.sort(key=lambda x: x["_match_score"], reverse=True)
    return out[:topk]


def list_local_datasets_summary() -> dict:
    """本地目录概要 (多源: scPlantDB + PlantScRNAdb)."""
    ds = load_local_datasets()
    if not ds:
        return {"n": 0}
    species_count = {}
    source_count = {}
    for d in ds:
        sp = d.get("species", "unknown")
        species_count[sp] = species_count.get(sp, 0) + 1
        src = d.get("source_db", "unknown")
        source_count[src] = source_count.get(src, 0) + 1
    return {"n": len(ds), "n_species": len(species_count),
            "species": species_count, "sources": source_count,
            "formats": ["h5ad", "rds"], "sources_list": ["scPlantDB", "PlantScRNAdb"]}
