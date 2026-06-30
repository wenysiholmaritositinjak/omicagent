---
name: plant_annotation
description: 植物单细胞细胞类型注释统一 — 用 scPlantDB ref 本体 + 版本化 mapping 表把文献/数据集的混乱命名统一到标准体系
phase: P3 (已集成)
---

# plant_annotation skill

## 何时用

- 用户要把某数据集的细胞类型注释"统一/标准化/映射到标准体系"
- 跨数据集/跨物种整合前需统一标签 (`run_cross_species` 前置 — 标签不统一整合无意义)
- 问"水稻根/拟南芥叶的细胞类型怎么统一命名"

## 核心机制: 表优先 (免费可复现, LLM 兜底回写)

`MetadataParser.map_to_standard` 三级解析:
1. **scPlantDB `plant_sc` 本体同义词精确归一** (最快, conf=1.0) — 80 个跨物种多组织标准名
2. **mapping_table.v1.json 查表** (持久化, 免费, 可复现) — 171 条已验证映射
3. **LLM 兜底** (未命中才调, 结果回写表 `status=auto`, 待人工 review)

效果: 覆盖率 P0 基线 14.3% → 表优先 76.7% (5.4 倍), LLM 调用 0 (全表命中).

## 工具

- `unify_annotation(path, species?, tissue?)` — 统一注释, 写回 `celltype_standard` 列, 输出统一后 h5ad
- `parse_metadata(path)` — 解析 obs + 映射 (含覆盖率/未映射项)

## 数据契约 (见 `omicagent/annotation/schemas.py`)

- `corpus.csv`: 文献级 raw_label 语料 (23 篇 × 210 行, 168 unique label)
- `mapping_table.v1.json`: `(raw_label, species, tissue)` → scPlantDB 标准名, 带 `method/confidence/status/provenance`
  - status: confirmed (人工确认) / auto (LLM 回写) / review (待人工) / rejected
- `scplantdb_ref.json`: scPlantDB 80 标准名 (ref 来源)

## 关键约束

- **表优先**: 查表 O(1) 免费, LLM 只处理未见标签并自动回写 → 表越用越全
- **三元 key**: `(raw_label, species, tissue)` — 同词在不同组织可映到不同 standard (跨组织消歧)
- **provenance 必填**: 每条映射带来源文献 + 原文证据 + 方法 + 状态 (可溯源, 比赛"真实可验证"卖点)
- **UNMAPPED 不瞎映射**: scPlantDB 无对应类型 (花器/胚乳/根特有) 诚实标 UNMAPPED, 待扩本体

## 当前覆盖 (v1)

- 范围: 叶/根/茎/花/种子/分生 × 拟南芥/水稻
- 已映射 127/171 (74%), UNMAPPED 44 (scPlantDB 盲区), review 15
- 命名变体统一样例: Vascular→Vascular tissue, Fibre→Fiber cell, Spongy parenchyma→Spongy mesophyll, guard cell/Guard cell→Guard cell

## 扩展

- 新文献注释: `annotation_harvester.harvest_paper()` 抽 raw_label → 跑 map_to_standard (回写表)
- 扩本体: scPlantDB 缺的类型 (Tapetum/Endosperm/Root cap) 人工补到 mapping_table + 本体
- 人工 review: 低置信 (<0.8) / UNMAPPED 项标 confirmed/rejected
