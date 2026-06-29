# 注释统一 · 基线 (现版 map_to_standard, 无持久表)

现注册本体: ['plant_leaf', 'default']
PLANT_LEAF_ONTOLOGY terms (11): ['Mesophyll', 'Guard_cell', 'Epidermis', 'Vascular', 'Bundle_sheath', 'Companion_cell', 'Xylem', 'Phloem_parenchyma', 'Fiber', 'Parenchyma_cell', 'Dividing_cell']

对叶/根/茎典型 raw_label 跑 ontology.normalize (仅同义词精确匹配, 不调 LLM):

| 组织 | 标签 | normalize 命中 |
|---|---|---|
| leaf | Mesophyll | Mesophyll |
| leaf | Guard cell | Guard_cell |
| leaf | Epidermis | Epidermis |
| leaf | Vascular | Vascular |
| leaf | Bundle sheath | Bundle_sheath |
| leaf | Bulliform cell | — (走 LLM) |
| leaf | Mesophyll cell | — (走 LLM) |
| leaf | Palisade | Mesophyll |
| root | Cortex | — (走 LLM) |
| root | Endodermis | — (走 LLM) |
| root | Root cap | — (走 LLM) |
| root | Stele | — (走 LLM) |
| root | Pericycle | — (走 LLM) |
| root | Trichoblast | — (走 LLM) |
| root | Xylem pole | — (走 LLM) |
| root | Lateral root cap | — (走 LLM) |
| stem | Epidermis | Epidermis |
| stem | Cortex | — (走 LLM) |
| stem | Vascular bundle | — (走 LLM) |
| stem | Parenchyma | Parenchyma_cell |
| stem | Sclerenchyma | — (走 LLM) |
| stem | Pith | — (走 LLM) |
| stem | Procambium | — (走 LLM) |

## 命中率 (仅 ontology 同义词, 不调 LLM)
| 组织 | 命中/总数 | 命中率 |
|---|---|---|
| leaf | 6/8 | 75% |
| root | 0/8 | 0% |
| stem | 2/7 | 29% |
| **合计** | **8/23** | **35%** |

## 结论
- 现版仅 `plant_leaf` 本体 (11 类), **无 plant_root / plant_stem 本体**, 根/茎 raw_label 全走 LLM 兜底.
- `map_to_standard` 的 LLM 映射**一次性、无持久化、无 provenance**, 每个数据集重问 LLM.
- **P2 目标**: 生成 plant_root/plant_stem 本体 + 版本化 mapping 表后, 根/茎命中率应大幅提升, 且 LLM 调用随表累积而减少 (表优先).