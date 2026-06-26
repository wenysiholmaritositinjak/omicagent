"""工具定义与执行 (供 Agent 工具循环调用).

每个工具有: name, description, params_schema (供 LLM 系统提示词), func (执行).
工具封装三能力 (检索/元数据/环境) + 跨物种 + 模块分析.
"""
from __future__ import annotations
import json, os, logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("omicagent.tools")


@dataclass
class ToolDef:
    name: str
    description: str
    params: dict          # {param: {type, description, required}}
    func: Callable
    # 执行结果是否需截断 (工具结果喂回 LLM 时空制长度)
    max_result_chars: int = 3000


class ToolRegistry:
    def __init__(self, llm=None, dispatcher=None):
        self.llm = llm
        self.dispatcher = dispatcher
        self.tools: dict[str, ToolDef] = {}
        self._register_defaults()

    def _register_defaults(self):
        self.register(ToolDef(
            "search_data",
            "检索单细胞/空间组学数据集 (NCBI GEO). 返回每条的 accession/物种/样本数/相关性, 以及数据类型(processed=处理好的rds/h5ad推荐, matrix=10x矩阵, raw=测序fastq, archive=tar包)、文件大小、文件列表. 结果按 processed 优先排序.",
            {"query": {"type": "string", "description": "自然语言检索需求, 如 '拟南芥叶片单细胞气孔'", "required": True},
             "topk": {"type": "integer", "description": "返回条数, 默认5", "required": False}},
            self._search_data, max_result_chars=5000,
        ))
        self.register(ToolDef(
            "list_local_data",
            "列出本地可用的单细胞 h5ad 数据文件 (供跨物种分析等使用). 无参数.",
            {},
            self._list_local_data, max_result_chars=1500,
        ))
        self.register(ToolDef(
            "download_data",
            "下载指定数据集 (按 accession). 优先下载处理好的 processed(rds/h5ad); 超过 max_size_gb(默认5G) 返回确认提示不下载. 返回文件类型/大小/路径.",
            {"accession": {"type": "string", "description": "GEO accession 如 GSE273926", "required": True},
             "dest": {"type": "string", "description": "下载目录, 默认 <data_dir>/<accession>", "required": False},
             "file_type": {"type": "string", "description": "processed(默认,处理好的rds/h5ad)/matrix(10x矩阵)/raw(测序)/all(全部)", "required": False},
             "max_size_gb": {"type": "number", "description": "超过此大小(GB)需手动确认, 默认5", "required": False}},
            self._download_data, max_result_chars=4000,
        ))
        self.register(ToolDef(
            "parse_metadata",
            "语义解析单细胞数据的 obs 字段: 识别细胞类型/样本/批次列, 将非标准注释映射为统一标准体系(植物叶片). 返回映射表与覆盖率.",
            {"path": {"type": "string", "description": "h5ad 文件路径", "required": True}},
            self._parse_metadata,
        ))
        self.register(ToolDef(
            "build_env",
            "根据数据元信息与分析目的, 自动判断所需工具(scanpy/seurat/saturn等)并搭建/复用 conda 环境. 返回环境名与验证结果.",
            {"metadata": {"type": "object", "description": "数据元信息 {species, modality, format, n_cells}", "required": True},
             "goal": {"type": "string", "description": "分析目的, 如 'cross-species integration'", "required": False}},
            self._build_env,
        ))
        self.register(ToolDef(
            "run_module",
            "运行单细胞分析模块 (qc/normalize/cluster/annotate/coexpression). 自动生成脚本并在指定 conda 环境执行.",
            {"module": {"type": "string", "description": "模块名: qc/normalize/cluster/annotate/coexpression", "required": True},
             "data_path": {"type": "string", "description": "输入 h5ad 路径", "required": True},
             "lang": {"type": "string", "description": "python 或 r, 默认 python", "required": False},
             "env": {"type": "string", "description": "conda 环境名, 如 scagent/seurat", "required": False}},
            self._run_module,
        ))
        self.register(ToolDef(
            "run_cross_species",
            "跨物种单细胞整合 (水稻+拟南芥等). 输入两物种 h5ad, 输出整合 UMAP PDF + 对齐分数 + 细胞类型映射. method: samap(BLAST同源,快) 或 saturn(ESM蛋白嵌入,深).",
            {"data1": {"type": "string", "description": "物种1 h5ad 路径", "required": True},
             "data2": {"type": "string", "description": "物种2 h5ad 路径", "required": True},
             "method": {"type": "string", "description": "samap 或 saturn, 默认 samap", "required": False}},
            self._run_cross_species, max_result_chars=4000,
        ))

    def register(self, tool: ToolDef):
        self.tools[tool.name] = tool

    def schema_for_llm(self) -> str:
        """生成供 LLM 系统提示词的工具描述."""
        lines = []
        for t in self.tools.values():
            params = ", ".join(f'{k}({v["type"]})' for k, v in t.params.items())
            req = ", ".join(k for k, v in t.params.items() if v.get("required"))
            lines.append(f"- {t.name}({params}): {t.description}")
            if req:
                lines.append(f"    必填: {req}")
        return "\n".join(lines)

    def execute(self, name: str, args: dict) -> str:
        """执行工具, 返回结果字符串 (喂回 LLM)."""
        if name not in self.tools:
            return f"错误: 未知工具 '{name}', 可用: {list(self.tools.keys())}"
        tool = self.tools[name]
        # 校验必填
        for p, spec in tool.params.items():
            if spec.get("required") and p not in args:
                return f"错误: 工具 {name} 缺少必填参数 {p}"
        try:
            log.info("执行工具 %s: %s", name, args)
            result = tool.func(**{k: v for k, v in args.items() if k in tool.params})
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > tool.max_result_chars:
                text = text[:tool.max_result_chars] + f"\n... (截断, 完整结果 {len(text)} 字符)"
            return text
        except Exception as e:
            log.exception("工具 %s 执行异常", name)
            return f"工具 {name} 执行出错: {e}"

    # ---- 工具实现 ----
    def _search_data(self, query, topk=5):
        from .data_searcher import DataSearcher
        from .ncbi_client import _human_size
        ds = DataSearcher(self.llm)
        rep = ds.search(query, topk=topk)
        recs = []
        for r in rep.records:
            recs.append({
                "accession": r.accession, "source": r.source_db, "species": r.species,
                "n_samples": r.n_samples, "title": r.title[:80], "relevance": round(r.relevance, 2),
                "download_url": r.download_url, "pubmed_id": r.pubmed_id,
                "data_type": r.data_type, "total_size": _human_size(r.total_size_bytes),
                "has_processed": r.has_processed,
                "files": [{"name": f["name"], "size": f["size_human"], "type": f["type"]} for f in r.files[:8]],
                "recommend": "processed" if r.has_processed else (r.data_type or "unknown"),
            })
        # processed 优先排序 (推荐处理好的数据)
        recs.sort(key=lambda x: (not x["has_processed"], -x["relevance"]))
        return {"n_records": len(recs), "records": recs, "elapsed": round(rep.elapsed, 1),
                "sources_ok": rep.sources_ok,
                "note": "data_type: processed=处理好的rds/h5ad(推荐), matrix=10x矩阵, raw=测序fastq, archive=tar包"}

    def _list_local_data(self):
        data_dir = Path(os.environ.get("OMICAGENT_DATA_DIR", str(Path.home() / "bioinfo" / "data")))
        h5ads = []
        for root in [data_dir, data_dir / "saturn_in5k", data_dir / "h5seurat"]:
            if root.exists():
                h5ads.extend(str(p) for p in root.glob("*.h5ad"))
        return {"local_h5ad": sorted(set(h5ads))[:20]}

    def _download_data(self, accession, dest=None, file_type="processed", max_size_gb=5):
        """下载数据, 优先 processed; 超过 max_size_gb 返回确认提示不下载."""
        from .ncbi_client import geo_suppl_files, _human_size
        from .data_searcher import DataSearcher
        files = geo_suppl_files(accession)
        if not files:
            return {"accession": accession, "error": "未找到 suppl 文件 (可能无公开数据或需 SRA 下载)"}
        # 按类型过滤
        if file_type == "processed":
            sel = [f for f in files if f["type"] == "processed"] or files
        elif file_type == "all":
            sel = files
        else:
            sel = [f for f in files if f["type"] == file_type] or files
        total = sum(f["size_bytes"] for f in sel)
        total_gb = total / 1024**3
        file_summary = [{"name": f["name"], "size": f["size_human"], "type": f["type"]} for f in sel]
        # 超过阈值, 返回确认提示 (不下载)
        if total_gb > max_size_gb:
            return {"accession": accession, "need_confirm": True,
                    "estimated_size_gb": round(total_gb, 2), "total_size": _human_size(total),
                    "file_type": file_type, "files": file_summary,
                    "msg": f"总大小 {total_gb:.1f}G 超过 {max_size_gb}G, 需手动确认. "
                           f"回复 '确认下载 {accession}' 或在 file_type=all 时指定."}
        # 下载
        ds = DataSearcher(self.llm)
        rec = type("R", (), {"accession": accession, "source_db": "GEO",
                              "download_url": _geo_url(accession)})()
        downloaded = []
        import subprocess
        d = Path(os.path.expanduser(dest or os.path.join(
            os.environ.get("OMICAGENT_DATA_DIR", "~/bioinfo/data"), accession)))
        d.mkdir(parents=True, exist_ok=True)
        for f in sel:
            out = d / f["name"]
            subprocess.run(["curl", "-sL", "-o", str(out), f["url"]],
                           check=False, timeout=1800)
            if out.exists() and out.stat().st_size > 0:
                downloaded.append(str(out))
        return {"accession": accession, "downloaded": downloaded, "n_files": len(downloaded),
                "file_type": file_type, "total_size": _human_size(total),
                "files": file_summary}

    def _parse_metadata(self, path):
        from .metadata_parser import MetadataParser
        from .ontology import load_ontology
        mp = MetadataParser(self.llm)
        a = mp.load(path)
        ins = mp.inspect_columns(a)
        out = {"celltype_col": ins.celltype_col, "sample_col": ins.sample_col,
               "n_cells": a.n_obs, "n_genes": a.n_vars}
        if ins.celltype_col:
            mr = mp.map_to_standard(a, ins.celltype_col, load_ontology("plant_leaf"))
            out["mapping"] = mr.mapping
            out["coverage"] = round(mr.coverage, 3)
            out["unmapped"] = mr.unmapped
        return out

    def _build_env(self, metadata, goal=""):
        from .env_builder import EnvBuilder
        eb = EnvBuilder(self.llm, self.dispatcher)
        spec = eb.analyze(metadata, analysis_goal=goal)
        result = eb.build(spec)
        return {"env": spec.env_name, "tools": spec.analysis_tools, "exists": spec.exists,
                "success": result.success, "missing": result.missing[:10]}

    def _run_module(self, module, data_path, lang="python", env=""):
        from .code_generator import CodeGenerator
        cg = CodeGenerator(self.llm, self.dispatcher)
        res = cg.generate_and_run(module, lang=lang, data_info=f"h5ad: {data_path}",
                                   task_desc=f"run {module}", env=env)
        return {"success": res.success, "stdout_tail": res.stdout[-800:], "stderr_tail": res.stderr[-400:]}

    def _run_cross_species(self, data1, data2, method="samap"):
        from .cross_species import run_cross_species
        r = run_cross_species(data1, data2, method=method)
        return {"success": r.success, "method": r.method, "h5ad": r.h5ad,
                "umap_pdfs": r.umap_pdfs, "alignment_score": round(r.alignment_score, 3),
                "mapping_csv": r.mapping_csv, "error": r.error, "summary": r.summary()}


def _geo_url(accession):
    import re
    acc = accession.strip().upper()
    m = re.match(r"(GSE\d+)$", acc)
    if not m:
        return ""
    num = m.group(1)
    return f"https://ftp.ncbi.nlm.nih.gov/geo/series/{num[:-3]}nnn/{num}/suppl/"
