# P2 映射表生成报告

> 阶段: P2 (mapping_table 生成) · 完成于 2026-06-30 · commit 6ae2a91, bc95f1d
> 产物: `data/annotation/mapping_table.v1.json` (171 条, raw_label→scPlantDB 标准名)

## 目标

用 P1 corpus 的 168 raw_label + scPlantDB `plant_sc` 本体 (80 标准名),
生成版本化 mapping 表, 把文献混乱命名统一到 scPlantDB 标准体系.

## 流程

```
corpus.csv (210 行, 168 raw_label)
   │  按 (raw_label, tissue) 聚合 paper_ids → 171 组合
   ▼
1) plant_sc ontology.normalize 精确匹配 (method=synonym_exact, conf=1.0) → 27 条
2) 未命中 144 条分批送 LLM (glm-5.2, 每批 15 + 80 标准术语池) → 选最匹配或 UNMAPPED
   ▼
mapping_table.v1.json (171 条, 带 provenance: paper_ids/method/confidence/status)
```

## 结果

| 指标 | 值 |
|---|---|
| 总条目 | 171 |
| 已映射 | 127 (74%) |
| UNMAPPED | 44 (scPlantDB 无对应: 花器/胚乳/根特有) |
| by method | synonym_exact 27 / llm_proposal 144 |
| by status | confirmed 156 / review 15 |
| LLM 调用 | 10 批 (约 3.5 万 token, 7 分钟) |

## 命名变体统一效果 (核心价值)

| 文献 raw_label | → scPlantDB 标准名 | method |
|---|---|---|
| Vascular | Vascular tissue | llm |
| Fibre | Fiber cell | llm (英式→美式) |
| Parenchyma cell | Parenchyll | llm |
| Spongy parenchyma | Spongy mesophyll | llm (粒度统一) |
| guard cell / Guard cell | Guard cell | synonym (大小写归一) |
| cortex / Cortex | Cortex | synonym |

聚合: 13 个 raw_label → Root epidermis; 8 → Meristematic cell / Initial cell (多文献命名收敛).

## 验收: 覆盖率提升 (表优先 vs P0 基线)

| 配置 | 覆盖率 | LLM 调用 |
|---|---|---|
| P0 基线 (plant_leaf 11 类, 无表) | 14.3% | 每次重问 |
| P2a (plant_sc 80 类本体, 无表) | 24.3% | 每次重问 |
| **P2b (plant_sc + mapping_table, 表优先)** | **76.7%** | **0 (全表命中)** |

**覆盖率 14.3% → 76.7% (5.4 倍), LLM 调用 0** — 表优先把一次性 LLM 映射升级为持久化查表,
省 token + 可复现 + 越用越全.

## UNMAPPED (44 条, 诚实标注 scPlantDB 盲区)

- 花器: Filament/Lodicule/Stigma/Tapetum/Pollen mother cell/Ovary wall/Ovule
- 胚乳/种子: Endosperm/Scutellum/Plumule
- 根特有: Root cap/Exodermis/LRC/CRC
- 叶: Bulliform cells/hydathodes cells
- 茎: Collenchyma

这些是 scPlantDB 命名体系的真实盲区, 标 UNMAPPED 比瞎映射好; 后续可扩充本体或人工补.

## 关键 fix

- `MappingStore.lookup` 逐级回退 (bc95f1d): 三元精确未命中时按 raw_label 回退,
  修复 map_to_standard 不传 species/tissue 时表查不到的问题.

## 下一步 (P3)

- skill 集成: 把 mapping_table + plant_sc 本体包成 `plant_annotation` skill, 注入 agent SYSTEM_PROMPT
- 人工 review 15 条低置信 + UNMAPPED 扩充本体
- 补采 P1 失败的 7 篇大图谱
- (v1.1) embedding 匹配器降 LLM 成本
