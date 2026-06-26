"""能力3: 语义理解组学元数据 (Metadata Parser).

利用大模型 + 文献原文, 自动解析 AnnData/Seurat 的 obs 字段语义:
- 识别哪列是细胞类型注释 / 样本分组 / 实验条件 / batch
- 将非标准细胞类型注释映射为统一标准体系 (见 ontology.py)
- 结合文献摘要作为映射上下文 (复用 NCBIClient)
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import config
from .llm_client import LLMClient
from .ncbi_client import NCBIClient
from .ontology import CellOntology, load_ontology, PLANT_LEAF_ONTOLOGY

log = logging.getLogger("omicagent.metadata")


@dataclass
class ColumnInspection:
    """obs 列语义识别结果."""
    celltype_col: str = ""
    sample_col: str = ""
    batch_col: str = ""
    condition_col: str = ""
    tissue_col: str = ""
    other: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MappingResult:
    column: str
    ontology: str
    mapping: dict       # {original_label: standard_label}
    confidence: dict    # {original_label: float}
    coverage: float     # 已映射细胞比例
    unmapped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class MetadataParser:
    def __init__(self, llm: Optional[LLMClient] = None,
                 ncbi: Optional[NCBIClient] = None):
        self.llm = llm or LLMClient()
        self.ncbi = ncbi or NCBIClient(api_key=getattr(config, "NCBI_API_KEY", None))

    # ---------- 载入 ----------
    def load(self, path: str):
        """载入 .h5ad (AnnData) 或 .rds (经 R 脚本导出 obs 的 csv)."""
        p = str(path)
        if p.endswith(".h5ad"):
            import scanpy as sc
            return sc.read_h5ad(p)
        if p.endswith(".csv"):
            import pandas as pd
            return pd.read_csv(p, index_col=0)
        if p.endswith(".rds"):
            raise ValueError("Rds 需先转 h5ad (SeuratDisk) 或导出 obs.csv, 见 01_data_prep/scripts")
        raise ValueError(f"不支持的格式: {p}")

    # ---------- obs 列语义识别 ----------
    def inspect_columns(self, adata) -> ColumnInspection:
        """LLM 识别 obs 各列语义 (细胞类型/样本/batch/条件/组织)."""
        import pandas as pd
        obs = adata.obs if hasattr(adata, "obs") else adata
        col_profile = {}
        for col in obs.columns:
            vals = obs[col].astype(str)
            uniq = vals.unique()
            col_profile[col] = {
                "n_unique": int(len(uniq)),
                "sample_values": list(uniq[:10]),
            }
        prompt = (
            "你是单细胞组学元数据解析助手. 下面是某数据集 obs 各列的统计. "
            "判断每列语义, 输出 JSON:\n"
            '{"celltype_col":"列名(细胞类型注释, 取值是细胞类型名)", '
            '"sample_col":"(样本/批次来源)", "batch_col":"(技术批次)", '
            '"condition_col":"(实验处理/条件)", "tissue_col":"(组织)", '
            '"other":{"其余列名":"一句话说明"}}\n'
            "若某类不存在, 对应值留空字符串. 只输出 JSON.\n"
            f"obs 列统计 (共 {len(obs)} 细胞):\n"
            + __import__('json').dumps(col_profile, ensure_ascii=False, indent=2)
        )
        try:
            data = self.llm.complete_json(prompt, task_type="complex", max_tokens=2048)
        except Exception as e:
            log.warning("LLM 列识别失败, 用规则兜底: %s", e)
            data = {}
        ins = ColumnInspection(
            celltype_col=data.get("celltype_col", "") or "",
            sample_col=data.get("sample_col", "") or "",
            batch_col=data.get("batch_col", "") or "",
            condition_col=data.get("condition_col", "") or "",
            tissue_col=data.get("tissue_col", "") or "",
            other=data.get("other", {}) or {},
        )
        # 兜底: LLM 未识别到细胞类型列时, 按列名关键词规则选
        if not ins.celltype_col:
            ins.celltype_col = self._guess_celltype_col(obs)
            if ins.celltype_col:
                log.info("LLM 未识别, 规则兜底选 celltype=%s", ins.celltype_col)
        log.info("列识别: celltype=%s sample=%s batch=%s", ins.celltype_col, ins.sample_col, ins.batch_col)
        return ins

    @staticmethod
    def _guess_celltype_col(obs) -> str:
        """列名规则兜底: 选最像细胞类型注释的列."""
        import pandas as pd
        candidates = []
        for col in obs.columns:
            cl = str(col).lower()
            # 优先: 含 celltype/cell_type/annotation/cluster_names/tissue_cluster
            if any(k in cl for k in ("celltype", "cell_type", "annotation", "tissue_cluster_names",
                                      "cluster_names", "final_annotation", "integrated_annotation")):
                # 且取值是离散类别(非数值), 唯一值数合理(2~50)
                nuniq = obs[col].astype(str).nunique()
                if 2 <= nuniq <= 60:
                    candidates.append((col, nuniq))
        if candidates:
            # 取唯一值数适中的(偏向更细注释)
            candidates.sort(key=lambda x: abs(x[1] - 8))
            return candidates[0][0]
        return ""

    # ---------- 文献上下文 ----------
    def fetch_paper_context(self, doi_or_pmid: str) -> str:
        """取文献摘要作为映射上下文 (pubmed id 优先)."""
        if not doi_or_pmid:
            return ""
        pid = str(doi_or_pmid).strip()
        # 多个 id 用分号分隔时取第一个
        pid = pid.split(";")[0].strip()
        if pid.isdigit():
            return self.ncbi.fetch_pubmed_abstract(pid)
        # DOI: 尝试 elink 反查 (简化: 直接返回空, 实际可调 crossref)
        log.info("非 pubmed id, 跳过文献摘要: %s", pid)
        return ""

    # ---------- 映射到标准体系 ----------
    def map_to_standard(self, adata, col: str,
                        ontology: Optional[CellOntology] = None,
                        paper_text: str = "",
                        confidence_threshold: float = 0.5) -> MappingResult:
        """把 obs[col] 的非标准注释映射到标准体系."""
        import pandas as pd
        ont = ontology or load_ontology("plant_leaf")
        obs = adata.obs if hasattr(adata, "obs") else adata
        if col not in obs.columns:
            raise ValueError(f"列不存在: {col}")
        labels = list(obs[col].astype(str).unique())

        # 先用 ontology 同义词快速归一
        mapping, confidence = {}, {}
        unmatched = []
        for lab in labels:
            std = ont.normalize(lab)
            if std:
                mapping[lab] = std
                confidence[lab] = 1.0
            else:
                unmatched.append(lab)

        # 未匹配的送 LLM 结合文献 + 本体映射
        if unmatched:
            llm_map = self._llm_map(unmatched, ont, paper_text)
            for lab, (std, conf) in llm_map.items():
                mapping[lab] = std
                confidence[lab] = conf
                if conf < confidence_threshold or std == "UNMAPPED":
                    pass  # 保留但标记低置信

        # 覆盖率
        vals = obs[col].astype(str)
        mapped_mask = vals.map(lambda v: v in mapping and mapping[v] != "UNMAPPED")
        coverage = float(mapped_mask.sum() / len(vals)) if len(vals) else 0.0
        unmapped_final = [l for l in labels if mapping.get(l) == "UNMAPPED" or l not in mapping]

        return MappingResult(
            column=col, ontology=ont.name, mapping=mapping,
            confidence=confidence, coverage=coverage,
            unmapped=unmapped_final,
        )

    def _llm_map(self, labels: list[str], ont: CellOntology, paper_text: str) -> dict:
        """LLM 把一批非标准标签映射到标准术语."""
        ctx = f"\n文献摘要(供参考):\n{paper_text[:1500]}\n" if paper_text else ""
        prompt = (
            "你是植物单细胞细胞类型注释标准化助手. 把以下非标准细胞类型标签映射到标准体系.\n"
            f"{ont.to_prompt()}\n{ctx}\n"
            f"待映射标签: {labels}\n"
            "规则: 优先映射到标准术语; 若确无对应, 填 'UNMAPPED'. "
            "输出 JSON 对象 {\"标签\": \"标准术语\"} 和置信度 {\"标签\": 0.0-1.0}, "
            '合并为 [{"label":"...","standard":"...","confidence":0.9}, ...] 数组. 只输出 JSON 数组.'
        )
        try:
            data = self.llm.complete_json(prompt, task_type="complex", max_tokens=1500)
        except Exception as e:
            log.warning("LLM 映射失败: %s", e)
            return {l: ("UNMAPPED", 0.0) for l in labels}
        out = {}
        if isinstance(data, list):
            for item in data:
                lab = item.get("label", "")
                out[lab] = (item.get("standard", "UNMAPPED"), float(item.get("confidence", 0.5)))
        return out

    # ---------- 应用映射 ----------
    def apply_mapping(self, adata, col: str, mapping_result: MappingResult,
                      out_col: str = "celltype_standard"):
        """把映射写回 obs 新列, 返回 adata."""
        import pandas as pd
        obs = adata.obs
        mp = {k: v for k, v in mapping_result.mapping.items() if v != "UNMAPPED"}
        obs[out_col] = obs[col].astype(str).map(mp).fillna("Unassigned")
        obs[out_col] = obs[out_col].astype("category")
        return adata

    # ---------- 一键汇总 ----------
    def summarize(self, adata, ontology: Optional[CellOntology] = None,
                  paper_text: str = "") -> dict:
        """一键: 识别列 -> 找细胞类型列 -> 映射标准 -> 返回报告."""
        ont = ontology or load_ontology("plant_leaf")
        ins = self.inspect_columns(adata)
        out = {"inspection": ins.to_dict(), "mapping": None}
        if ins.celltype_col:
            mr = self.map_to_standard(adata, ins.celltype_col, ont, paper_text)
            out["mapping"] = mr.to_dict()
        return out
