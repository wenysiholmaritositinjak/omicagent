"""OmicAgent 注释统一子包 (annotation).

把能力3 的一次性 LLM 映射升级为持久化 + 可累积 + 可溯源的版本化 mapping 表,
并以 skill 形式被 agent 调用. 产物喂给能力4 (cross_species 依赖统一标签列).

模块:
- schemas:        corpus 行 + mapping 表条目 dataclass 与校验
- zotero_source:  只读 zotero.sqlite 筛选植物单细胞文献
- pdf_extractor:  pymupdf 抽 PDF 文本块 + 表格, 定位注释段落
- annotation_harvester: 协调 条目→PDF→LLM 抽注释→写 corpus (P1 主体)
- mapping_store:  版本化 mapping 表 CRUD/查询/diff (P2 主体)
"""
