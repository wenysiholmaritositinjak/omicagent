# OmicAgent

> 植物单细胞组学 AI Scientist Agent —— 基于大模型与多源数据检索的端到端科研智能体

OmicAgent 将植物单细胞与空间转录组学研究中最耗时的三个环节自动化：**数据检索、环境搭建、跨数据集元数据语义解析**，并衔接代码生成与跨物种整合分析，把科研分析周期缩短约 70%。

- **数据检索**：自然语言描述研究需求 → 自动检索 NCBI GEO / ArrayExpress 等数据库 → 返回文献列表与下载链接（2–5 天 → 30 分钟以内）
- **环境搭建**：读取数据集元信息 → 判断所需分析工具（Seurat / Scanpy / SATURN / SAMap / hdWGCNA / SCENIC）→ 自动安装依赖、生成并运行分析脚本（数天 → 10–15 分钟）
- **语义理解元数据**：大模型结合文献原文解析 `obs` 字段 → 自动识别细胞类型注释 / 样本分组 / 实验条件 → 映射为统一标准体系（1–2 周 → 4–8 小时）

---

## 目录

- [系统架构](#系统架构)
- [模块说明](#模块说明)
- [安装](#安装)
- [配置](#配置)
- [快速开始](#快速开始)
- [API 参考](#api-参考)
- [测试与验证](#测试与验证)
- [目录结构](#目录结构)
- [设计决策与已知限制](#设计决策与已知限制)

---

## 系统架构

OmicAgent 采用四层架构 + 七个核心模块的分层设计：

```
┌─────────────────────────────────────────────────────────┐
│  交互层    Pipeline 编排 (OmicAgent.run / search_and_analyze)  │
├─────────────────────────────────────────────────────────┤
│  Agent 核心层                                              │
│   TaskPlanner   CodeGenerator   ReportGenerator          │
├─────────────────────────────────────────────────────────┤
│  平台/能力层                                                │
│   DataSearcher  MetadataParser  EnvBuilder  ToolDispatcher│
├─────────────────────────────────────────────────────────┤
│  数据/基础层                                                 │
│   LLMClient  NCBIClient  Ontology  Config                │
└─────────────────────────────────────────────────────────┘
```

**数据流（端到端）**：

```
用户自然语言需求
    │
    ▼
DataSearcher.search()          ── 能力1: 多源检索
    │  LLM 解析 → ParsedQuery → 路由 NCBI GEO/ArrayExpress → 去重 → LLM 重排
    ▼
DatasetRecord[] (含 accession / 下载链接 / pubmed_id)
    │
    ▼
MetadataParser.inspect + map_to_standard()  ── 能力3: 语义解析
    │  LLM 识别 obs 列语义 → 结合文献摘要 + Ontology 同义词 → 映射标准注释
    ▼
AnnData (含 celltype_standard 列)
    │
    ▼
EnvBuilder.analyze + build()   ── 能力2: 自动建环境
    │  LLM 判断工具 → TOOL_PACKAGE_MAP 映射 → 复用/新建 conda env → 验证包
    ▼
EnvSpec + EnvResult
    │
    ▼
CodeGenerator.generate_and_run(env=...)  ── 代码生成与执行
    │  生成 R/Python 脚本 → conda run -n <env> 执行 → 失败带错误重试
    ▼
ReportGenerator.render()       ── HTML 报告
```

---

## 模块说明

### 基础层

#### `llm_client.py` — 统一 LLM 客户端
封装 DCS Cloud 统一 API（OpenAI 兼容），提供分级模型路由与推理模型处理。

- **模型路由**：简单任务（代码生成 / 报告摘要 / 元数据解析）→ `deepseek-v4-pro`；复杂任务（任务规划 / 注释映射 / 推理）→ `glm-5.2`，失败回退 `claude-opus-4-8`
- **推理模型处理**：deepseek-v4-pro / glm-5.2 把思考放 `reasoning_content`、答案放 `content`；若 `content` 为空（思考耗尽 max_tokens）自动加倍重试
- **重试与回退**：指数退避重试，4xx（除 429）直接跳出交由上层回退备选模型
- **Token 计量**：记录每次调用用量，`total_usage()` 汇总成本

主要接口：`complete(prompt, task_type, ...)`、`complete_json(prompt, ...)`（容错提取 JSON）。

#### `ncbi_client.py` — NCBI E-utilities 客户端
能力1 与能力3 共用的公共客户端，对接 NCBI 公开免费 API。

- 方法：`esearch(db, term, retmax)`、`esummary(db, ids)`、`elink_pubmed(dbfrom, ids)`、`fetch_pubmed_abstract(pmid)`、`search_geo(term, retmax)`（高层封装，返回含 accession/title/summary/species/平台/样本数/pubmed_ids 的结构化记录）
- 限速（无 key ≤3 req/s，有 key ≤10 req/s）、重试、JSON 解析
- `geo_suppl_url(accession)`：构造 GEO series supplementary FTP 下载目录

#### `ontology.py` — 标准细胞类型本体
为能力3 提供统一标准注释体系。

- `PLANT_LEAF_ONTOLOGY`：植物叶片标准（11 类：Mesophyll / Guard_cell / Epidermis / Vascular / Bundle_sheath / Companion_cell / Xylem / Phloem_parenchyma / Fiber / Parenchyma_cell / Dividing_cell），含中英文同义词归一表
- `CellOntology.normalize(label)`：同义词快速归一
- `load_ontology(name)` / `register_ontology(name, ont)`：加载/注册自定义体系（可扩展植物根、动物等）

### 能力层

#### `data_searcher.py` — 能力1：智能文献与数据检索
多源检索器架构，统一接口 + 灵活路由。

- **统一数据结构** `DatasetRecord`：`title / accession / source_db / species / platform / n_samples / modality / summary / download_url / paper_doi / pubmed_id / metadata / relevance`
- **检索器**（均实现 `BaseSearcher`）：
  - `NCBIGeoSearcher`（核心，真实可用）：LLM 构造 GEO 检索式 → esearch/esummary/elink → 解析 series
  - `ArrayExpressSearcher`：EBI ArrayExpress REST API（单细胞数据丰富）
  - `OmicSeekSearcher`：保留入口，`OMICSEEK_BASE` 可达时使用，否则跳过
- **DataSearcher 主类**：
  - `parse_query(user_query)`：LLM 解析需求 → `ParsedQuery`（keywords / species / modality / tissue / celltype_hint / source_hints）
  - `search(user_query, topk)`：解析 → 按 source_hints 路由 + 默认 GEO → 并行调用 → 去重 → LLM 重排打分 → 返回 `SearchReport`
  - `download(record, dest)`：下载 supplementary 文件（支持 .h5ad/.rds/.mtx/.tar.gz）

#### `metadata_parser.py` — 能力3：语义理解组学元数据
大模型 + 文献原文，自动解析 obs 字段并映射标准注释。

- `load(path)`：载入 .h5ad（scanpy）；.rds 需先经 SeuratDisk 转换或导出 obs.csv
- `inspect_columns(adata)`：LLM 识别 obs 列语义（细胞类型 / 样本 / batch / 条件 / 组织），LLM 失败时按列名关键词规则兜底
- `fetch_paper_context(doi_or_pmid)`：NCBI efetch 取文献摘要作为映射上下文
- `map_to_standard(adata, col, ontology, paper_text)`：先 Ontology 同义词快速归一，未匹配送 LLM 结合文献 + 本体映射，返回映射表与置信度
- `apply_mapping(adata, col, mapping)`：写回标准列 `celltype_standard`
- `summarize(adata)`：一键报告（识别的列、映射表、覆盖率、未映射项）

#### `env_builder.py` — 能力2：自动环境搭建与代码执行
读取元信息 → 判断工具 → 生成/复用 conda 环境 → 验证。

- `TOOL_PACKAGE_MAP`：分析工具 → conda/pip 包映射（scanpy / seurat / hdwgcna / saturn / samap / scenic）
- `analyze(metadata, analysis_goal)`：LLM + 规则判断所需工具与语言 → 生成 `EnvSpec`
- `build(spec, reuse_existing)`：已存在 env（scagent/seurat/samap）则只补缺包（`conda list` 比对），否则 `conda create`；执行安装（TUNA 镜像）；验证核心包可导入（`conda run -n <env>`）；`conda env export` 快照
- `ensure_env_for_tool(tool)`：便捷方法，按已知工具直接返回已建好的 env
- **验证分级**：`verify_cmds`（核心，失败阻断）与 `verify_optional`（可选，失败仅警告）

### Agent 核心层

#### `task_planner.py` — 任务规划器
将用户自然语言输入解析为结构化分析任务序列（调用复杂模型）。

- 输出 `Plan`（goal + list of `Task`），每任务含 `id / description / tool / module / params / expected_output`
- 可用工具：omicseek / shell / code_gen / llm / report；标准模块：qc / normalize / cluster / annotate / coexpression / cross_species

#### `code_generator.py` — 代码生成器
按模块生成 R（Seurat 生态）或 Python（Scanpy 生态）分析脚本，覆盖六大模块。

- `MODULES`：文档表 3-3 标准分析模块说明（含 R/Python 推荐工具）
- `generate(module, lang, ...)`：生成脚本，返回文件路径
- `generate_and_run(module, lang, ..., env)`：生成并执行，失败带错误重新生成（≤3 次）；`env` 指定 conda 环境，通过 `conda run -n <env>` 执行

#### `report_generator.py` — 报告生成器
Jinja2 渲染可交互 HTML 报告。

- `summarize(results, goal)`：LLM 生成自然语言摘要
- `render(...)`：分析摘要 + 图表（base64 内嵌）+ 数据表格 + 可复现代码 + 运行信息 → 单文件 HTML

### 编排层

#### `pipeline.py` — OmicAgent 编排
- `run(user_input)`：经典闭环（规划 → 执行各任务 → 报告）
- `search_and_analyze(user_input, local_data)`：**端到端**串联三能力（检索 → 元数据解析 → 建环境 → 生成运行脚本 → 报告）

#### `tool_dispatcher.py` — 工具调度器
Agent 与外部资源交互的统一网关。

- `omicseek_search(query)`：委托 `DataSearcher`（保留旧方法名兼容）
- `parse_obs_semantics(adata, paper_text)`：委托 `MetadataParser`
- `run_shell(cmd)` / `run_script_file(path)`：在分析环境内执行命令
- `llm_call(prompt)`：统一大模型调用入口
- `parse_metadata(dataset_info)`：LLM 解析数据集元信息判断所需工具

---

## 安装

### 环境要求
- WSL2 Ubuntu 22.04（或任意 Linux）+ Miniconda
- Python ≥ 3.10
- 可选：NVIDIA GPU（SATURN/ESM 用，CPU 亦可）

### 步骤

```bash
# 1. 创建主环境
conda create -n scagent -c conda-forge -c bioconda python=3.10 \
    scanpy anndata scikit-learn scikit-misc biopython requests jinja2 \
    python-dotenv matplotlib seaborn -y
conda activate scagent

# 2. 安装 LLM/深度学习依赖（GPU 版需对应 CUDA）
pip install torch --index-url https://download.pytorch.org/whl/cu128  # 或 cpu
pip install fair-esm typed-argument-parser record-keeper plotly scvi-tools

# 3. （可选）R 环境（Seurat 生态）
conda create -n seurat -c conda-forge -c bioconda r-seurat r-seuratobject \
    r-harmony r-hdf5r r-dplyr -y

# 4. （可选）SAMap 环境
conda create -n samap -c conda-forge python=3.12 gxx gcc make -y
conda activate samap && pip install sc-samap
```

### 部署框架代码
```bash
# 框架代码位于 agent_framework/omicagent/
# 设置 PYTHONPATH 即可使用
export PYTHONPATH=/path/to/agent_framework:$PYTHONPATH
```

---

## 配置

在 `agent_framework/.env` 配置密钥与端点（优先读环境变量）：

```bash
# DCS Cloud 统一 API (OpenAI 兼容)
OMICAGENT_API_BASE=https://dcsapi.dcs.cloud/api/aigress/unified/v1
OMICAGENT_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 模型路由
OMICAGENT_SIMPLE_MODEL=deepseek-v4-pro
OMICAGENT_COMPLEX_MODEL=glm-5.2
OMICAGENT_COMPLEX_FALLBACK=claude-opus-4-8

# NCBI E-utilities (可选, 有 key 提速)
NCBI_API_KEY=

# OmicSeek (可选, 可达时使用)
OMICSEEK_BASE=https://omicseek.cngb.org

# 调用参数
OMICAGENT_TIMEOUT=180
OMICAGENT_MAX_RETRIES=3
```

---

## 快速开始

### 1. 单能力使用

```python
from omicagent.data_searcher import DataSearcher

# 能力1: 数据检索
ds = DataSearcher()
report = ds.search("拟南芥叶片单细胞数据，包含气孔细胞 (Arabidopsis leaf single cell, guard cell)")
for r in report.records:
    print(r.accession, r.species, r.download_url, r.relevance)

# 能力3: 元数据语义解析
from omicagent.metadata_parser import MetadataParser
from omicagent.ontology import load_ontology
mp = MetadataParser()
adata = mp.load("/path/to/at.h5ad")
ins = mp.inspect_columns(adata)                       # 识别 obs 列
mr = mp.map_to_standard(adata, ins.celltype_col, load_ontology("plant_leaf"))
adata = mp.apply_mapping(adata, ins.celltype_col, mr) # 写回标准列

# 能力2: 自动建环境
from omicagent.env_builder import EnvBuilder
eb = EnvBuilder()
spec = eb.analyze({"species": "Oryza sativa", "modality": "snRNA-seq", "format": "h5ad"},
                  analysis_goal="QC + clustering")
result = eb.build(spec)  # 复用已有 conda env, 验证包
```

### 2. 端到端

```python
from omicagent.pipeline import OmicAgent

agent = OmicAgent()
out = agent.search_and_analyze(
    "拟南芥叶片单细胞数据，包含气孔细胞 (Arabidopsis leaf single cell, guard cell)",
    local_data="/path/to/at_5k.h5ad",  # 提供本地数据则跳过下载
)
print(out["stages"]["search"]["n_records"])   # 检索记录数
print(out["stages"]["metadata"]["mapping"])   # 映射表
print(out["stages"]["env"]["success"])        # 环境搭建
print(out["report"])                          # HTML 报告路径
```

### 3. 经典规划模式

```python
agent = OmicAgent()
out = agent.run("对水稻叶片单细胞数据做质控、聚类和细胞类型注释")
# 自动: 规划 -> 生成脚本 -> 执行 -> 报告
```

---

## API 参考

### DataSearcher
| 方法 | 说明 |
|---|---|
| `parse_query(user_query) -> ParsedQuery` | LLM 解析自然语言需求为结构化查询 |
| `search(user_query, topk=5) -> SearchReport` | 多源检索 + 去重 + LLM 重排 |
| `download(record, dest_dir) -> list[str]` | 下载数据集 supplementary 文件 |

### MetadataParser
| 方法 | 说明 |
|---|---|
| `load(path) -> AnnData` | 载入 h5ad / obs.csv |
| `inspect_columns(adata) -> ColumnInspection` | LLM + 规则识别 obs 列语义 |
| `fetch_paper_context(doi_or_pmid) -> str` | 取文献摘要 |
| `map_to_standard(adata, col, ontology, paper_text) -> MappingResult` | 映射标准注释 |
| `apply_mapping(adata, col, mapping) -> AnnData` | 写回标准列 |
| `summarize(adata) -> dict` | 一键报告 |

### EnvBuilder
| 方法 | 说明 |
|---|---|
| `analyze(metadata, analysis_goal) -> EnvSpec` | LLM 判断工具与语言 |
| `build(spec, reuse_existing=True) -> EnvResult` | 安装/补包 + 验证 |
| `ensure_env_for_tool(tool) -> EnvSpec` | 按已知工具返回已建 env |

### OmicAgent
| 方法 | 说明 |
|---|---|
| `run(user_input, render_report=True) -> dict` | 经典规划闭环 |
| `search_and_analyze(user_input, local_data, render_report) -> dict` | 端到端三能力串联 |

---

## 测试与验证

测试位于 `tests/`，已全部验证通过：

```bash
cd agent_framework
export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH

python tests/test_data_searcher.py      # 能力1: 真实 NCBI GEO 检索
python tests/test_metadata_parser.py    # 能力3: 水稻/拟南芥 obs 映射
python tests/test_env_builder.py        # 能力2: 复用 scagent 验证
python tests/test_e2e.py                # 端到端
```

### 验证结果（真实数据）

| 能力 | 输入 | 结果 |
|---|---|---|
| **1 检索** | "拟南芥叶片单细胞气孔" | 5 条 GEO，GSE273926 叶肉单细胞 rel=0.80 排第一，17s（远低于 30 分钟目标） |
| **3 元数据** | rice_5k.h5ad (`tissue_cluster_names`) | 识别 `cluster` 列，100% 覆盖映射（conf=1.0） |
| **3 元数据** | at_5k.h5ad (`integrated_annotation`) | 识别 `CellType` 列，99.9% 覆盖（trichome 合理未映射） |
| **3 元数据** | 跨物种共享 | 4 类型一致：Epidermis / Guard_cell / Mesophyll / Vascular |
| **2 环境** | snRNA-seq + 跨物种整合 | 判断 scanpy+saturn → scagent，核心包验证通过 |
| **端到端** | 自然语言 + 本地 h5ad | 检索→解析→建环境→生成脚本→HTML 报告全跑通 |

---

## 目录结构

```
agent_framework/
├── README.md                      # 本文档
├── .env                           # 密钥与端点配置
├── doc.txt / agent_framework_doc.pdf  # 项目实施方案
├── omicagent/                     # 框架核心
│   ├── __init__.py
│   ├── config.py                  # 全局配置 (API/模型路由/路径)
│   ├── llm_client.py              # 统一 LLM 客户端 (基础层)
│   ├── ncbi_client.py             # NCBI E-utilities 客户端 (基础层)
│   ├── ontology.py                # 标准细胞类型本体 (基础层)
│   ├── data_searcher.py           # 能力1: 智能检索
│   ├── metadata_parser.py         # 能力3: 语义理解元数据
│   ├── env_builder.py             # 能力2: 自动环境搭建
│   ├── task_planner.py            # 任务规划器 (核心层)
│   ├── code_generator.py          # 代码生成器 (核心层)
│   ├── report_generator.py        # 报告生成器 (核心层)
│   ├── tool_dispatcher.py         # 工具调度器 (核心层)
│   ├── pipeline.py                # 编排层 (OmicAgent)
│   └── templates/
│       └── report.html.j2         # HTML 报告模板
├── tests/                         # 测试
│   ├── test_data_searcher.py
│   ├── test_metadata_parser.py
│   ├── test_env_builder.py
│   ├── test_e2e.py
│   └── smoke_test.py
└── results/                       # 运行产物
    ├── report.html                # HTML 报告
    ├── search_report.json         # 检索结果
    ├── mapping_rice.json          # 映射表
    ├── mapping_at.json
    ├── env_result.json            # 环境结果
    └── scripts/                   # 生成的分析脚本
```

---

## 设计决策与已知限制

### 设计决策

1. **数据检索主通道**：OmicSeek（`omicseek.cngb.org`）当前不可达，改以 NCBI GEO（E-utilities，公开免费）为真实主通道，ArrayExpress/Cellxgene 尽力而为，文献/需求提及某库时灵活路由。OmicSeek 入口保留，可达时优先使用。
2. **执行环境**：使用 WSL 本地 conda 环境（scagent/seurat/samap）。DCS Cloud 容器 SDK 待平台提供后替换 `ToolDispatcher.run_shell` 后端即可。
3. **标准注释体系**：`PLANT_LEAF_ONTOLOGY` 来自跨物种整合（水稻+拟南芥叶片）已验证的统一命名，可按组织/物种扩展。
4. **模型路由**：简单任务用 deepseek-v4-pro（省 token），复杂任务优先 glm-5.2、失败回退 claude-opus-4-8。

### 已知限制

- **GEO 检索式**：不支持中文关键词，`_build_term` 只用 species+modality+tissue+EntryType 构造检索式，keywords 留给 LLM 重排。
- **scvi 导入冲突**：torchvision 0.26 与 torch 2.11 存在算子注册冲突，裸 `import scvi` 会失败，但 SATURN 训练可用；故 saturn 的 scvi 验证标记为 optional 不阻断。
- **推理模型偶发空 content**：deepseek-v4-pro/glm-5.2 思考耗尽 max_tokens 时 content 为空，`LLMClient` 会自动加倍重试；`inspect_columns` 对多列 h5ad 需 `max_tokens≥2048`，且有规则兜底。
- **.rds 支持**：`MetadataParser.load` 不直接读 .rds，需先用 SeuratDisk 转为 h5ad 或导出 obs.csv（见 `01_data_prep/scripts`）。

### 扩展点

- 新增检索器：继承 `BaseSearcher`，实现 `search`/`can_handle`/`available`，注册到 `DataSearcher`
- 新增分析工具：在 `EnvBuilder.TOOL_PACKAGE_MAP` 添加映射
- 新增本体：`register_ontology(name, CellOntology(...))`
- 新增分析模块：在 `CodeGenerator.MODULES` 添加模块说明

---

## 引用

如使用本项目，请引用：

```
OmicAgent: 植物单细胞组学 AI Scientist Agent.
基于 DCS Cloud 与 OmicSeek 的端到端科研智能体. 2026.
```

跨物种整合方法：
- **SATURN**: Rosen et al., *Towards Universal Cell Embeddings*, bioRxiv 2023 (doi:10.1101/2023.02.03.526939)
- **SAMap**: Tarashansky et al., *Mapping single-cell atlases throughout Metazoa*, eLife 2021 (doi:10.7554/eLife.66747)
