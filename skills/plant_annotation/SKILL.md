---
name: plant_annotation
description: 植物单细胞细胞类型注释统一 — 把多物种文献/数据集的混乱命名统一到 scPlantDB 标准体系, 保留特异群, 输出 celltype_standard+celltype_subtype 列接 SAMap/SATURN
phase: P3 (已集成, 13 物种)
---

# plant_annotation skill

## 一句话

**统一植物单细胞数据集的细胞类型注释** — 把文献里混乱的命名（Vascular/Vascular tissue、Fibre/Fiber cell、guard cell/Guard cell）统一到 scPlantDB 80 标准名, 保留特异群, 写回 `celltype_standard`(SAMap用) + `celltype_subtype`(SATURN用/保特异群) 两列, 作为跨物种整合前置。

## 何时用

- 跨数据集/跨物种整合前需统一标签（`run_cross_species` 前置 — 标签不统一整合无意义）
- 用户说"统一/标准化/映射细胞类型注释"
- 多个数据集合并前消除命名差异

## 核心机制: 表优先 (免费可复现, LLM 兜底回写)

`MetadataParser.map_to_standard` 三级解析:
1. **scPlantDB `plant_sc` 本体同义词精确归一** (最快, conf=1.0) — 80 跨物种标准名
2. **mapping_table.v4.json 查表** (持久化, 免费, 可复现) — 400 条已验证映射, 13 物种
3. **LLM 兜底** (未命中才调, 结果回写表 `status=auto`, 待人工 review)

效果: 覆盖率 P0 基线 14% → 表优先 76%, LLM 调用 0 (全表命中). 反向覆盖 scPlantDB 74% (59/80 标准名有文献支撑).

## 两级映射 (粗+细)

每条映射带 `standard` (粗) + `subtype` (细):
- `INIT.cortex` → standard=`Initial cell`, subtype=`cortex-initial`
- `guard mother cells` → standard=`Guard cell`, subtype=`mother-cell`
- `EVB xylem cells` → standard=`Xylem`, subtype=`EVB`
- `Tapetum` (scPlantDB盲区) → standard=`UNMAPPED`, subtype=`tapetum` (保留特异群)

**SAMap 用 `celltype_standard` 跨物种对齐; SATURN 用 `celltype_subtype` 保留亚群/特异群.**

## 怎么用

### 方式1: Agent 对话式 (推荐)

启动 `omicagent` CLI, 直接说:
```
统一 /mnt/d/omicagent/test_data/rice_leaf.h5ad 和 at_leaf.h5ad 的细胞注释
```
Agent 自动调 `unify_annotation` 工具, 表优先统一, 写回 `_unified.h5ad`.

### 方式2: 编程式 API

```python
from omicagent.tools import ToolRegistry
from omicagent.llm_client import LLMClient
reg = ToolRegistry(llm=LLMClient())
# 统一单个数据集
result = reg.execute("unify_annotation", {
    "path": "/mnt/d/omicagent/test_data/rice_leaf.h5ad",
    "species": "rice", "tissue": "leaf"
})
# result: {mapping, coverage, standard_col, subtype_col, out_h5ad, ...}
```

### 方式3: 模拟演示 (不依赖 scanpy/h5ad)

```bash
python skills/plant_annotation/demo_unify.py
```
用 corpus 真实文献注释模拟两个叶片数据集, 端到端展示统一+写回+跨物种对齐.

## 输入输出

**输入**: h5ad (含细胞类型注释列, 如 `celltype`/`CellType`/`integrated_annotation`)
- `.rds` 需先转 h5ad 或导出 obs.csv (见下"rds 转换")

**输出**: `{name}_unified.h5ad`, obs 新增两列:
- `celltype_standard`: 粗粒度标准名 (scPlantDB 80 之一, 或 `UNMAPPED`)
- `celltype_subtype`: 亚群修饰词 (SATUN 用) 或 `specific:{原名}` (特异群标记)

## rds 转换 (R → Python)

unify_annotation 不直接读 .rds. 用 R 导出:
```r
library(Seurat)
obj <- readRDS("integrated_leaf.rds")
# 拟南芥6时期取一个子集 (假设时期列叫 "stage")
obj_sub <- subset(obj, subset = stage == "stage1")
# 导出 obs (meta.data) 为 csv (轻量, 仅注释列)
write.csv(obj_sub@meta.data, "at_leaf_stage1_obs.csv")
# 或导出 h5ad (需 SeuratDisk)
# library(SeuratDisk)
# SaveH5Seurat(obj_sub, "at_leaf_stage1.h5Seurat")
# then: h5Seurat → h5ad via SeuratDisk::Convert
```
Python 端 `unify_annotation` 接收导出的 csv/h5ad.

## 数据产物 (data/annotation/)

| 文件 | 内容 |
|---|---|
| `scplantdb_ref.json` | scPlantDB 80 标准名 ref (从 app.js 扒取) |
| `mapping_table.v4.json` | 400 条两级映射, 13 物种, 带 provenance |
| `corpus.csv` | 545 行文献注释语料 (23篇Zotero+PubMed, 13物种) |
| `p1_harvest_report.md` / `p2_mapping_report.md` | 采集/映射报告 |

## 13 物种覆盖

Arabidopsis / rice / maize / soybean / tomato / cotton / cassava / tobacco / periwinkle / barrelclover / wheat / cabbage / poplar
(组织: root 185 / leaf 108 / stem 56 / meristem 54 / flower 36 / seed 18)

## 关键约束

- **表优先**: 查表 O(1) 免费, LLM 只处理未见标签并自动回写 → 表越用越全
- **特异群不强行映射**: scPlantDB 无对应类型 (花器/胚乳/根特有) 标 `UNMAPPED + specific:` 保留原名
- **provenance 必填**: 每条映射带来源文献 + 证据 + 方法 + 状态 (可溯源)

## 扩展

- 新文献注释: `annotation_harvester.harvest_paper()` 抽 raw_label → 跑 map_to_standard (回写表)
- 扩本体: scPlantDB 缺的类型 (Tapetum/Endosperm/Root cap) 人工补到 mapping_table + ref
- 新物种: `ncbi_client.esearch` 搜 PubMed + `fetch_pubmed_fulltext` 抽注释 (见 `_pubmed_gap.py` 思路)
