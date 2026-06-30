# P1 文献注释采集报告

> 阶段: P1 (annotation_harvester 批量采集) · 完成于 2026-06-30 · commit b3c7fd5
> 产物: `data/annotation/corpus.csv` (23 篇 × 210 行, 168 unique raw_label)

## 目标

从 Zotero 文献 PDF 批量抽取细胞类型注释 (raw_label + marker + evidence),
产出文献级语料 `corpus.csv`, 为 P2 生成版本化 mapping 表提供原料.
映射目标 = scPlantDB `plant_sc` 本体 (见 `ref_ontology.py`).

## 采集流程

```
zotero_index.json (178 篇植物单细胞文献)
   │  强匹配筛选: single-cell/图谱 + rice/Arabidopsis, 排除 综述/human/method
   ▼
v1 子集 31 篇 (有 PDF)
   │  每篇: pdf_extractor 抽注释段+表格 → 段落筛选(强信号优先, 12K字符上限)
   ▼
LLM (glm-5.2, max_tokens 8192) 抽 [{raw_label, marker, tissue, evidence, confidence}]
   │  去重(同 raw_label 合并 marker) + 黑名单过滤(Unknown/NA...)
   ▼
CorpusRow → corpus.csv (增量写, 每篇完成即写盘, 支持断点续跑)
```

## 结果

| 指标 | 值 |
|---|---|
| v1 子集 | 31 篇 (强匹配 single-cell + rice/Arabidopsis) |
| 成功采集 | 23 篇 (74%) |
| 失败 | 8 篇 (大图谱 glm-5.2 超时 / 思考耗尽 content 空) |
| 总行数 | 210 |
| unique raw_label | 168 |
| confidence | high 207 / mid 2 / low 1 (99% high) |

### by tissue
- root 112 (最丰富, 根尖全区室: Atrichoblast/Trichoblast/Cortex/Endodermis/Root cap/QC/Columella/LRC/XPP/INIT.*)
- leaf 53 (Mesophyll/Guard cell/Bundle sheath/Bulliform cells/Pavement...)
- flower 18, meristem 11, stem 8, seed 4, other 4

### by species
- Arabidopsis 115 / rice 95 (干净, 无笛卡尔积污染)

### 每篇抽取条数 (前 5)
- 1931 水稻图谱 29 条 | 394 水稻根比较 21 | 291 拟南芥时空轨迹 16 | 15 综述 16 | 389 拟南芥多组学 14

## 质量观察 (印证"注释混乱"设想)

同物多名 (P2 统一对象):
- guard cell / Guard cell / guard cells / guard mother cells
- cortex / Cortex / cortical / INIT.cortex
- Mesophyll / Spongy parenchyma / Palisade (粒度不一)
- Fibre / Fiber cell (英式/美式)

粒度差异: 文献 raw_label 偏粗或偏细, scPlantDB 命名体系更规范 → P2 映射空间大.

## 失败篇 (8 篇, 可补采)

主因: 大图谱 PDF → glm-5.2 推理慢 (>180s 超时) 或思考耗尽 max_tokens content 空.
补采策略: 调高 LLM timeout (300s) + 减输入字符 (8K) + 单篇重试.
- Pluripotency acquisition (超时)
- A single-cell multi-omics atlas (思考耗尽) [注: 1931 已成功, 此为另一条]
- High-throughput single-cell (思考耗尽)
- A single-cell spatial transcriptomic (超时)
- A single-cell transcriptome at (思考耗尽)
- An Arabidopsis single-nucleus (思考耗尽)
- The single-cell stereo-seq (思考耗尽)

## 过程踩坑与修复 (4 个 fix commit)

| 问题 | 修复 | commit |
|---|---|---|
| WSL2 空闲关 VM 杀长任务 | `.wslconfig vmIdleTimeout=-1` | (Windows 配置) |
| 中途崩丢全部数据 | harvest_batch 增量写 + 断点续跑 | bb687cf |
| species/tissue 笛卡尔积污染 | 取单值去串 | 9f4d75e |
| 非图谱文混入 + 单篇 PDF 崩 | 收紧筛选 + PDF try 兜底 | 39fe3fd |

## 下一步 (P2)

用 corpus.csv 的 168 raw_label 跑改造后 `map_to_standard` (ontology=plant_sc, mapping_store)
→ 生成版本化 `mapping_table.v1.json` (raw_label → scPlantDB 标准名), 人工 review 低置信/跨组织歧义.
对比 P0 基线 (plant_leaf 叶75%/根0%/茎29%) 看覆盖率提升.
