# OmicAgent — Claude Code 工作准则 (Ponytail: 懒惰资深开发者模式)

> 核心理念：最好的代码是你没写的代码。写最少必要代码，但绝不砍验证/错误处理/安全。

## 懒惰阶梯 (写代码前依次判断，命中即停)

1. **这需要存在吗？** → 不需要就跳过 (YAGNI)
2. **代码库里已经有了？** → 复用，别重写
3. **标准库能做？** → 用标准库
4. **平台原生功能？** → 用原生
5. **已装的依赖能做？** → 用它，别引新依赖
6. **一行能搞定？** → 一行
7. **最后才写**：能工作的最少代码

懒惰是对**解决方案**懒，不是对**读代码**懒：改之前先读懂 touched 代码与真实流程，再选阶梯 rung。

## 永不砍 (lazy, not negligent)

- 信任边界验证、数据丢失处理、安全、可访问性
- API key / 密钥处理（见 .gitignore，.env 永不入库）
- NCBI 限速（避 429）、LLM 重试回退
- 中文 UTF-8 编码处理（r.encoding=utf-8, stdout reconfigure）

## OmicAgent 项目特定

- 对话式 CLI：`omicagent` 命令，工具循环用提示词 JSON（适配推理模型）
- 三能力：数据检索(NCBI GEO+PubMed+植物专用库) / 环境搭建(conda) / 元数据语义(LLM+Ontology)
- 跨物种：SAMap(BLAST) + SATURN(ESM1b)
- 改代码时：优先复用已有模块（data_searcher/metadata_parser/env_builder/cross_species），不重复造轮子
- 生成分析脚本时遵循阶梯：能 scanpy 一行的不写十行，能复用 conda env 的不新建
- 注释统一（`omicagent/annotation/` 子包）：`map_to_standard` 表优先（ontology 同义词 → `mapping_store` 查表 → LLM 兜底回写，`status=auto`）；三元 key `(raw_label, species, tissue)` 避跨组织同词异义；UNMAPPED 不回写表；新增采集/解析代码复用 schemas/zotero_source/pdf_extractor，不重复造轮子
