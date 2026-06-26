"""单细胞/空间组学数据库目录 (Catalog).

预置主流数据库元信息 (数据类型/样本/文献/下载链接/API). 检索优先查 catalog 里的库(快),
再网络搜索补充. 用户可随时刷新可用性 / 追加新库.

- load_catalog(): 读 data/db_catalog.json
- search_catalog(query, species, tissue): 按需求匹配数据库, 返回优先级列表
- update_catalog(): 网络刷新各库 api_available (HEAD 检测)
- add_database(...): 用户追加新库
- export_table(): 表格输出
"""
from __future__ import annotations
import json, logging, os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("omicagent.catalog")

CATALOG_FILE = Path(__file__).resolve().parent / "data" / "db_catalog.json"


@dataclass
class DatabaseEntry:
    name: str
    url: str
    api: str = ""
    api_available: bool = False
    search_method: str = "page"        # api / page / python_pkg
    scope: str = "general"             # general / plant / human / brain / spatial / cancer
    species: str = "all"
    data_types: list = None
    has_sample_info: bool = False
    has_papers: bool = False
    has_download: bool = False
    priority: int = 99
    notes: str = ""


def load_catalog() -> dict:
    """读 catalog, 返回 {version, databases:[...]}."""
    try:
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("读 catalog 失败: %s", e)
        return {"version": "unknown", "databases": []}


def save_catalog(cat: dict) -> Path:
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    return CATALOG_FILE


def _match_scope(scope: str, species: str, tissue: str) -> int:
    """scope 与需求匹配度评分 (高=更相关)."""
    s = (species or "").lower()
    if scope == "plant" and any(x in s for x in ["arabidopsis", "oryza", "zea", "plant", "rice", "maize"]):
        return 10
    if scope == "human" and "human" in s:
        return 8
    if scope == "brain" and ("brain" in (tissue or "").lower() or "brain" in s):
        return 8
    if scope == "spatial" and "spatial" in (tissue or "").lower():
        return 8
    if scope == "general":
        return 5
    return 3


def search_catalog(query: str = "", species: str = "", tissue: str = "",
                   only_api: bool = False) -> list[dict]:
    """按需求匹配数据库, 返回优先级排序的列表.

    only_api: 只返回有 API 的库 (用于快速检索)
    """
    cat = load_catalog()
    out = []
    for db in cat.get("databases", []):
        if only_api and not db.get("api_available"):
            continue
        scope_score = _match_scope(db.get("scope", ""), species, tissue)
        # 关键词匹配 (name/notes/species)
        q = (query or "").lower()
        text = (db.get("name", "") + " " + db.get("notes", "") + " " + db.get("species", "")).lower()
        kw_score = 3 if q and any(k in text for k in q.split()) else 0
        score = scope_score + kw_score - db.get("priority", 99) * 0.1
        out.append({**db, "_match_score": round(score, 2)})
    out.sort(key=lambda x: x["_match_score"], reverse=True)
    return out


def update_catalog(timeout: int = 10) -> dict:
    """网络刷新各库 api_available (并发 HEAD/GET 检测), 保存回 catalog."""
    cat = load_catalog()
    dbs = cat.get("databases", [])

    def _check(db):
        url = db.get("api") or db.get("url")
        if not url:
            return
        try:
            import requests
            r = requests.get(url, timeout=timeout, allow_redirects=True)
            db["api_available"] = (r.status_code < 500)
            db["_last_check"] = f"{r.status_code}"
        except Exception as e:
            db["api_available"] = False
            db["_last_check"] = f"err:{type(e).__name__}"
        return db

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_check, dbs))
    cat["version"] = __import__("datetime").date.today().isoformat()
    save_catalog(cat)
    ok = sum(1 for d in dbs if d.get("api_available"))
    return {"checked": len(dbs), "available": ok, "version": cat["version"]}


def add_database(name: str, url: str, api: str = "", search_method: str = "page",
                 scope: str = "general", species: str = "all", data_types: list = None,
                 notes: str = "", **extra) -> dict:
    """用户追加新库到 catalog."""
    cat = load_catalog()
    # 去重
    if any(d.get("name") == name for d in cat.get("databases", [])):
        return {"error": f"数据库 {name} 已存在"}
    entry = {
        "name": name, "url": url, "api": api,
        "api_available": bool(api), "search_method": search_method,
        "scope": scope, "species": species,
        "data_types": data_types or [], "has_sample_info": True,
        "has_papers": True, "has_download": True,
        "priority": len(cat.get("databases", [])) + 1, "notes": notes,
    }
    entry.update(extra)
    cat.setdefault("databases", []).append(entry)
    save_catalog(cat)
    return {"added": name, "total": len(cat["databases"])}


def export_table() -> list[dict]:
    """表格形式返回所有库 (供展示)."""
    cat = load_catalog()
    rows = []
    for d in cat.get("databases", []):
        rows.append({
            "name": d.get("name", ""),
            "scope": d.get("scope", ""),
            "api_available": "✓" if d.get("api_available") else "✗",
            "search_method": d.get("search_method", ""),
            "species": d.get("species", "")[:25],
            "data_types": ", ".join(d.get("data_types", []))[:40],
            "sample_info": "✓" if d.get("has_sample_info") else "✗",
            "papers": "✓" if d.get("has_papers") else "✗",
            "download": "✓" if d.get("has_download") else "✗",
            "url": d.get("url", ""),
        })
    return rows
