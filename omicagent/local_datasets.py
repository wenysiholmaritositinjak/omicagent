"""本地数据集目录 (scPlantDB 等已知数据集, 含 h5ad/rds 下载).

检索时优先匹配本地目录(快, 已知数据), 再网络搜索补充.
"""
from __future__ import annotations
import json, logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("omicagent.local_datasets")

DATA_FILE = Path(__file__).resolve().parent / "data" / "scplantdb_datasets.json"


def load_local_datasets() -> list[dict]:
    """加载本地数据集目录."""
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8")).get("datasets", [])
    except Exception as e:
        log.warning("读本地数据集失败: %s", e)
        return []


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
        d_ts = (d.get("tissue") or "").lower()
        # 物种匹配 (拉丁名部分匹配, 如 arabidopsis 命中 Arabidopsis thaliana)
        if sp and (sp in d_sp or d_sp in sp):
            score += 10
        # 组织匹配 (部分, 如 root 命中 Root tip/Whole root)
        if ts and (ts in d_ts or d_ts in ts):
            score += 8
        # 关键词匹配 (拆词, 任一命中加分; 搜 publication/tissue/species)
        if kw:
            text = (d.get("publication", "") + " " + d.get("tissue", "") + " " + d.get("species", "")).lower()
            for w in kw.split():
                if len(w) > 2 and w in text:
                    score += 3
        if score > 0 or not (sp or ts or kw):
            out.append({**d, "_match_score": score})
    out.sort(key=lambda x: x["_match_score"], reverse=True)
    return out[:topk]


def list_local_datasets_summary() -> dict:
    """本地目录概要."""
    ds = load_local_datasets()
    if not ds:
        return {"n": 0}
    species_count = {}
    for d in ds:
        sp = d.get("species", "unknown")
        species_count[sp] = species_count.get(sp, 0) + 1
    return {"n": len(ds), "species": species_count,
            "formats": ["h5ad", "rds"], "source": "scPlantDB"}
