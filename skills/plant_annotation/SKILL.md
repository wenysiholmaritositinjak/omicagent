---
name: plant_annotation
description: 植物单细胞细胞类型注释统一 — 把文献/PDF 中的非标准注释映射到统一标准体系，沉淀为版本化 mapping 表
phase: P3 (占位, P0 骨架)
---

# plant_annotation skill (占位 · P3 实现)

## 何时用

- 用户要把某数据集/文献的细胞类型注释"统一/标准化/映射到标准体系"
- 跨数据集/跨物种整合前需要统一标签 (`cross_species` 前置)
- 问"水稻根的单细胞注释有哪些命名/怎么统一"

## 工作流 (P3 定稿)

1. **取源**: `ZoteroSource.list_plant_sc_papers()` 筛文献 → `pdf_extractor` 抽注释段落+表格
2. **抽 raw_label**: LLM 从段落+表格抽 (raw_label, marker, tissue, evidence) → 写 `corpus.csv`
3. **统一映射**: `MetadataParser.map_to_standard(mapping_store=..., species=, tissue=)`
   - 三级: ontology 同义词精确 (conf=1.0) → `mapping_store` 查表 (免费可复现) → LLM 兜底 (回写表, `status=auto`)
   - 未命中且 LLM 给 UNMAPPED 的, 标记待人工 review
4. **人工 review**: 低置信 / 跨组织歧义 (如 parenchyma 在叶/根/茎) → 标 `confirmed`/`rejected`

## 关键约束

- **表优先**: 查表 O(1) 免费, LLM 只处理未见标签并自动回写 → 表越用越全
- **三元 key**: `(raw_label, species, tissue)` — 同词在不同组织可映到不同 standard
- **provenance 必填**: 每条映射带来源文献 + 原文证据 + 方法 + 状态 (可溯源, 比赛"真实可验证"卖点)
- **UNMAPPED 不回写表**

## 数据契约 (见 `omicagent/annotation/schemas.py`)

- `CorpusRow`: 文献级 raw_label 语料
- `MappingEntry`: `key=(raw_label,species,tissue)` → `standard`, 带 `method/confidence/status/provenance`

## 当前状态 (P0)

- ✅ schemas / zotero_source / pdf_extractor / mapping_store / map_to_standard 表优先改造
- ⬜ annotation_harvester (P1) / 批量采集 (P1) / 表生成+review (P2) / 注入 SYSTEM_PROMPT (P3)
