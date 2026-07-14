# 仓库事实核查审计证据文档

> 审计日期: 2026-07-14
> 审计方式: 普通文件字节指纹、Chroma 二次副本逻辑核查、数据库系统元数据只读 SELECT、仓库外隔离测试
> 仓库: E:\3\posgresql\1
> 分支: master
> 基础 HEAD: 42be3c0e59324f6fa0767ae69b45fa6962aa8729

---

## 1. 审计环境

### 1.1 Git 状态

```
分支: master
HEAD: 42be3c0e59324f6fa0767ae69b45fa6962aa8729
```

审计前已修改的跟踪文件:
- `vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin` (M)
- `vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin` (M)
- `vanna_data/chroma.sqlite3` (M)

审计前未跟踪文件:
- `tools/audit_level1_chroma_coverage.py`
- `tools/full_levels1_3_regression.py`
- `tools/full_levels1_3_regression_result.md`
- `tools/level1_chroma_coverage_result.md`
- `tools/level1_chroma_inventory.json`

### 1.2 本阶段正式数据指纹

指纹算法：按相对路径排序，对每个普通文件记录 `相对路径 + 文件长度 + SHA-256`，再对规范化清单计算 SHA-256。正式目录只读取普通文件字节；未通过 Chroma、Vanna Memory 或 SQLite 打开。

| 目录 | 文件数 | 本阶段开始清单 SHA-256 | 本阶段结束清单 SHA-256 | 结论 |
|------|------:|-------------------------|-------------------------|------|
| `vanna_data/` | 5 | `83fe3fb3c7b735f3b665a8105a8e8d705f801c1da467dd9274292ce732ec1ee5` | `83fe3fb3c7b735f3b665a8105a8e8d705f801c1da467dd9274292ce732ec1ee5` | **未变更** |
| `agent_data/` | 110 | `c0d4ecf8733ce08121e7b19f59a2d96fece2fbb2d19a72f898cc387515a49a73` | `c0d4ecf8733ce08121e7b19f59a2d96fece2fbb2d19a72f898cc387515a49a73` | **未变更** |

本阶段开始时，正式 `vanna_data/` 已有 3 个既存工作区修改。此前文档把这些变化归因于“搜索时 HNSW 自动重组，非数据变更”，但没有审计前精确副本和内容级对比支撑，故撤销该结论。本阶段不恢复、不覆盖、不暂存这些文件。

### 1.3 Chroma 可信基线与内容级比较

- **可信基线**：`E:\3\_backup\level3_p2_training_20260713_164829`。
- **创建时间**：2026-07-13 16:48:29。
- **来源与选择理由**：Level 3 P2 训练时创建的最近正式备份；当前记录中的 P2 写入时间约为 16:51，说明该备份位于 P2 写入前。没有找到上一阶段审计开始前的临时副本，故它是任务指定优先级下最近的可信基线，但不是三项正式文件发生二进制变化前的精确审计基线。
- **读取隔离**：基线和当前正式目录各先复制为第一副本，再复制为读取副本；所有 Chroma/SQLite 内容读取仅针对 `baseline_read_copy` 与 `current_read_copy`。
- **结论边界**：无法证明二进制指纹变化前后的绝对一致性；只能证明当前正式目录与最近可信备份之间的逻辑差异。禁止据此声称“仅索引二进制变化”。

比较规范：document 统一换行符并去除首尾空白；metadata 将 `args_json`、`metadata_json` 解析后按键排序、使用紧凑 JSON；所有集合型结果按 ID 排序后计算 SHA-256。

| 指标 | 可信基线读取副本 | 当前正式目录读取副本 | 一致 |
|------|------------------|----------------------|------|
| collection 名称 / 数量 | `tool_memories` / 1 | `tool_memories` / 1 | 是 |
| collection 记录数 | 63 | 72 | 否 |
| record ID 集哈希 | `0d78797f9bd04ff8323f9de93f677f26523c3f9a8bc0e4e66b46b96c46011af6` | `09a0561d3efebb73d32366b03322342704c41626849d6db666df434d1afff7a2` | 否 |
| document 规范化内容哈希 | `b45654790b37b66d2eb0b71b8bc6a8228d326d87f5c0fbba6c9c549417584b49` | `fd85ca097ea768746cfd618e239a9c58860b5838ccf6ecd1cb49012472f03a56` | 否 |
| metadata 规范化 JSON 哈希 | `de61840a7db8d0f7aacd0d07a2139725a9e556ba45b1601fba4eba59900d5896` | `fd494cb766f2c6097d40902b252177699c986a47c3b6cffedc7d2b3c8a969545` | 否 |
| embedding 数量 | 63 | 72 | 否 |
| `is_text_memory` 分布 | true=8，缺失=55 | true=8，缺失=64 | 否 |
| `training_level` 分布 | L2=16，P0=18，P1=21，缺失=8 | L2=16，P0=18，P1=21，P2=9，缺失=8 | 否 |
| `train_decision` 分布 | approved=55，缺失=8 | approved=64，缺失=8 | 否 |
| `tool_name` 分布 | run_sql=55，缺失=8 | run_sql=64，缺失=8 | 否 |

基线的 55 个 sample_id 为 L2 16 个、P0 18 个、P1 21 个；当前新增 9 个 P2 sample_id：`L3_P2_SQL_001` 至 `L3_P2_SQL_008` 及 `L3_P2_SQL_011`。无删除 ID；新增 record ID 为：

`13935be4-e76d-4340-b1d5-c125d8e79828`、`25af065d-4d92-4750-9f25-7824c4065e1e`、`3b61e715-8086-41e4-aeee-deb74bce6caa`、`42491fda-23ef-48e0-b961-f18c6b76167e`、`6330fbac-97f0-4f57-bc86-7fe7b6741dba`、`a9169c7c-3fea-4e9a-b578-f703975227bb`、`b55cfce0-f4e4-4c7a-be3e-a80d7440e612`、`e8e727c9-8570-4dbb-9a03-c13805c69a74`、`fe848313-435e-47a5-8877-44e372d75039`。

**内容级结论：逻辑内容存在差异。** 差异与 9 条 P2 approved Tool Memory 的新增一致；由于缺少精确审计前基线，不能判断此前 3 个正式文件的二进制变化是否还包含其他只影响索引的变化。

---

## 2. 仓库和依赖事实

### 2.1 Python 环境

- **Python 版本**: 3.12.4
- **虚拟环境**: `E:\3\posgresql\1\vanna_venv\`
- **Vanna 版本**: 2.0.2
- **Vanna 安装方式**: editable install from `vanna_src/`
- **Vanna 安装位置**: `E:\3\posgresql\1\vanna_venv\Lib\site-packages` (editable: `E:\3\posgresql\1\vanna_src`)
- **Vanna 依赖**: chromadb, click, httpx, pandas, plotly, pydantic, PyYAML, requests, sqlalchemy, sqlparse, tabulate
- **Embedding 模型**: `BAAI/bge-small-zh-v1.5` (SentenceTransformer, 中文)
- **依赖文件**: 无 `requirements.txt` 或 `pyproject.toml` (项目级别)。Vanna 通过 git+https  editable install 安装。其余依赖为隐式依赖。

### 2.2 不存在正式依赖清单

项目根目录无 `requirements.txt`、`requirements-dev.txt`、`Pipfile`、`Pipfile.lock`、`poetry.lock`、`pyproject.toml` (项目级)。依赖通过 `pip install` 手动管理，无法复现精确依赖版本。

### 2.3 不存在多个 Python 环境冲突

仅检测到 `vanna_venv/` 一个虚拟环境。无 `.venv/`、`venv/` 或其他 Python 虚拟环境目录。

---

## 3. 当前真实运行架构

### 3.1 主服务入口

**文件**: `step4_server.py` (行 1-189)

**LLM 配置** (行 102-105):
```python
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"
```

### 3.2 Agent 创建链路 (`step4_server.py:create_agent()`, 行 121-179)

**完整链路 (从外到内)**:

```
OptimizedSystemPromptBuilder (追加本项目规则)
    ↓
Agent(
    llm_service=OpenAILlmService(deepseek-v4-pro @ api.deepseek.com)
    tool_registry=ToolRegistry(GuardedRunSqlTool)
    user_resolver=SimpleUserResolver → User(id="demo")
    agent_memory=ChineseChromaAgentMemory(embedding=BAAI/bge-small-zh-v1.5, threshold=0.55)
    llm_context_enhancer=SqlExampleContextEnhancer (第3层)
        └── base_enhancer=DeterministicMetadataContextEnhancer (第2层)
            └── base_enhancer=DefaultLlmContextEnhancer (第1层: Vanna 默认检索)
    config=AgentConfig(stream_responses=True)
)
```

### 3.3 注册的工具

**仅一个工具** (`step4_server.py` 行 137-148):
- `GuardedRunSqlTool` — 包装 `RunSqlTool`, 执行前通过 `SQLGuard.validate()` 校验
- `SQLGuard` 实例在 `GuardedRunSqlTool` 和 `SqlExampleContextEnhancer` 之间共享 (行 141)

**未注册的工具**:
- `VisualizeDataTool` — **未注册**（与 `step4_agent.py` 和文档描述不同。React 前端自行生成 ECharts。）

### 3.4 Context Enhancer 顺序

三层层叠 (Decorator 模式):

1. **DefaultLlmContextEnhancer** (第1层): Vanna 默认检索 → `search_text_memories()` 注入 DDL/文档。**但实际只检索 text memories，不检索 tool usage**。
2. **DeterministicMetadataContextEnhancer** (第2层): 从 `agent_data/column_metadata_index.json` 确定性匹配候选表+字段，追加 "Deterministic Metadata Context" 到 system prompt。
3. **SqlExampleContextEnhancer** (第3层): 通过 `memory.search_similar_usage(tool_name_filter="run_sql")` 检索 approved SQL 示例，经过 SQLGuard 二次校验后注入 "Retrieved Approved SQL Examples" 到 system prompt。

### 3.5 Memory 实例

`ChineseChromaAgentMemory` (`agent_config.py` 行 25-35):
- 继承 `ChromaAgentMemory`
- 重写 `search_text_memories`: 默认阈值从 0.7 降到 0.55
- 持久化目录: `vanna_data/`
- Embedding: `BAAI/bge-small-zh-v1.5`

### 3.6 UserResolver

`SimpleUserResolver` (`step4_server.py` 行 112-118):
- 固定返回 `User(id="demo", username="demo")`
- 无认证/授权逻辑

### 3.7 SQLGuard 接入位置

SQLGuard 在两个位置生效:

1. **执行时**: `GuardedRunSqlTool.execute()` (`tools/guarded_run_sql_tool.py` 行 35-68) — 每次 run_sql 前校验 SQL
2. **检索时**: `SqlExampleContextEnhancer._candidate_to_example()` (`tools/sql_example_context_enhancer.py` 行 141-149) — SQL 示例注入 system prompt 前二次校验

### 3.8 自动保存/在线学习逻辑

**不存在。** 主服务 (`step4_server.py`) 不含 `memory.save_tool_usage()` 或 `memory.save_text_memory()` 调用。所有训练写入仅通过独立训练脚本 (`training/train_sql_examples_level*.py`) 手动执行。

`DefaultLlmContextEnhancer` (Vanna 内置) 也 **不调用** `save_tool_usage()`。确认方式: 搜索全仓 `save_tool_usage` 调用点 (见 `tools/diagnose_llm_context_injection.py` 行 212: `"default_mentions_save_tool_usage": "save_tool_usage" in default_source` 结果为 False)。

### 3.9 绕过当前 Guard/metadata/SQL Example Enhancer 的旧脚本

| 脚本 | 绕过内容 | 影响 |
|------|---------|------|
| `step4_agent.py` | SQLGuard, DeterministicMetadataContextEnhancer, SqlExampleContextEnhancer | 使用 RunSqlTool 直通，无 SQL 校验、无确定性元数据、无 SQL 示例检索 |
| `step4_test2.py` | 同上 + 无 OptimizedSystemPromptBuilder | 同上，且无 system prompt 规则约束 |
| `diag_tool_calls.py` | 同上 | 同上 |

这三个脚本使用 `OPENCODE_API_KEY` + `https://opencode.ai/zen/go/v1`，而当前主服务使用 `DEEPSEEK_API_KEY` + `https://api.deepseek.com`。

---

## 4. Memory 和 Chroma 实际状态

### 4.1 ChromaDB Collection

**仅一个 collection**: `tool_memories` (id: `e69ae44d-d7b5-4585-98ef-b2bcf6f00e09`)

Vanna ChromaAgentMemory 将 text memories 和 tool usage 存储在同一 collection 中，通过 `is_text_memory` 标记区分。

### 4.2 记录分布

**总 embeddings**: 72

| 类型 | 数量 | 内容 |
|------|------|------|
| Text Memory (Level 1 DDL) | 6 | 6 张白名单表的 DDL (含中文注释) |
| Text Memory (Level 1 SQL 示例) | 2 | 2 条旧式示例问答 (存入时无 training metadata) |
| Tool Usage (Level 2) | 16 | `level2_sql_examples`, approved run_sql |
| Tool Usage (Level 3 P0) | 18 | `level3_p0_sql_examples`, approved run_sql |
| Tool Usage (Level 3 P1) | 21 | `level3_p1_sql_examples`, approved run_sql |
| Tool Usage (Level 3 P2) | 9 | `level3_p2_sql_examples`, approved run_sql |

### 4.3 Training Level 分布

| Training Level | 数量 | Train Decision | 是否完整 |
|---------------|------|---------------|---------|
| `level2_sql_examples` | 16 | 全部 approved | ✅ |
| `level3_p0_sql_examples` | 18 | 全部 approved | ✅ |
| `level3_p1_sql_examples` | 21 | 全部 approved | ✅ |
| `level3_p2_sql_examples` | 9 | 全部 approved | ✅ |
| (文本记忆, 无 training_level) | 8 | N/A | N/A |

### 4.4 数据质量检查

- **重复 sample_id 组**: **0**；64 个 sample_id 唯一
- **缺少 training_level**: **0** (全部 64 个 tool usage 均有 training_level)
- **缺少 sample_id**: **0** (全部 64 个 tool usage 均有 sample_id)
- **缺少 train_decision**: **0** (全部 64 个 tool usage 均有 train_decision)
- **重复 question 组**: **0**；64 个 `question_hash` 唯一
- **重复 SQL 组**: **0**；64 个 `sql_hash` 唯一
- **重复 question+SQL 组**: **0**；64 个组合哈希唯一

内容去重不再由 sample_id 唯一性间接推断。question 规范化包括去除首尾空白和连续空白归一化；SQL 规范化包括去除首尾空白、连续空白归一化、末尾分号归一化和 SQL 关键字大写归一化，字符串字面量保持不变。随后分别计算 SHA-256。

### 4.5 Level 1 DDL 覆盖

仅覆盖 `train_step3.py` 定义的 6 张白名单表:
- `rs_outlet`
- `rs_outlet_info_v2`
- `rs_outlet_monitor_v2`
- `rs_outlet_live_v2`
- `rs_outlet_trace_v2`
- `rs_outlet_remediation_v2`

其余 **109 张有 metadata index 的表无 DDL 在 ChromaDB 中**。`column_metadata_index.json` 覆盖了 115 张表的结构，但 ChromaDB 仅含 6 张表的 DDL。

### 4.6 Level 2/3 Approved 样本实际数量

| Level | 实际数量 | 训练脚本声称数量 | 一致 |
|-------|---------|---------------|------|
| Level 2 | 16 | 16 (L2_SQL_001-018, 缺 011,012) | ✅ |
| Level 3 P0 | 18 | 18 (L3_P0_SQL_001-018) | ✅ |
| Level 3 P1 | 21 | 21 (L3_P1_SQL_001-024, 缺 004,005,010) | ✅ |
| Level 3 P2 | 9 | 9 (L3_P2_SQL_001-011, 缺 009,010) | ✅ |
| **合计** | **64** | — | — |

这些编号不是“草案中不存在”，而是有 review 证据的冻结或排除样本：

| ID | 存在于草案 | 最终 decision | 未写入原因 | 证据 |
|----|------------|---------------|------------|------|
| `L2_SQL_011` | 是 | `requires_manual_review` | 排污口溯源责任主体统计属中风险业务语义，字段口径未人工确认 | `training/sql_examples_level2_draft.json`、`training/sql_examples_level2_review.md`、`training/sql_examples_level2_training_result.md` |
| `L2_SQL_012` | 是 | `requires_manual_review` | 溯源企业、排放许可证和信用代码字段口径未人工确认 | 同上 |
| `L2_SQL_019` | 是 | `requires_manual_review` | SQLGuard 为 warning，且 `wm_water_source_intake_v2` 不在 deterministic candidate tables，业务场景稳定性待确认 | 同上 |
| `L3_P1_SQL_004` | 是 | `requires_manual_review` | `has_abnormal` 未提供允许值、示例值或枚举，无法确认固定值“是”真实存在 | `training/sql_examples_level3_p1_review_result.json`、`training/sql_examples_level3_p1_review_report.md` |
| `L3_P1_SQL_005` | 是 | `requires_manual_review` | `is_remediated` 未提供允许值、示例值或枚举，无法确认固定值“是”真实存在 | 同上 |
| `L3_P1_SQL_010` | 是 | `requires_manual_review` | `has_sampling_condition` 未提供允许值、示例值或枚举，无法确认固定值“是”真实存在 | 同上 |
| `L3_P2_SQL_009` | 是 | `excluded` | J5 真实精确匹配为 0；水文站为 4 位城市码、区县表为 6 位编码，JOIN 被真实数据否定 | `training/sql_examples_level3_p2_review_result.json`、`training/sql_examples_level3_p2_review_report.md`、`training/level3_p2_join_feasibility_result.md` |
| `L3_P2_SQL_010` | 是 | `excluded` | 同一 J5 编码层级不兼容，精确 JOIN 匹配为 0 | 同上 |

---

## 5. Metadata Index 来源及数据库差异

### 5.1 `column_metadata_index.json` 来源

- **文件**: `agent_data/column_metadata_index.json`
- **大小**: 115 表, 2572 条字段记录
- **结构**: `{table, table_comment, column, type, comment}`
- **Git 引入**: commit `79ba667` ("Add deterministic metadata retriever"), 2026-07-08
- **生成脚本**: **未找到。** 全仓搜索无任何脚本生成此文件。该文件被直接提交到仓库。
- **使用者**:
  - `tools/sql_guard.py` (行 13) — SQL 静态校验
  - `tools/metadata_retriever.py` (行 12) — 确定性元数据检索
  - 各训练检查脚本 — 表和字段存在性验证

### 5.2 数据库对象数量与集合差异

| 指标 | 数量 | 说明 |
|------|-----:|------|
| `public` BASE TABLE | **162** | `pg_class.relkind='r'` |
| `public` VIEW | **5** | `pg_class.relkind='v'` |
| `public` MATERIALIZED VIEW | **0** | `pg_class.relkind='m'` |
| 表/视图对象合计 | **167** | 162 + 5 |
| Metadata Index 对象 | **115** | 2572 条字段记录，115 个唯一 `table` 值 |
| DB 表/视图 - Index | **52** | 47 个 BASE TABLE + 5 个 VIEW |
| Index - DB 表/视图 | **0** | index 对象全部存在 |
| Index 中属于 VIEW 的对象 | **0** | 115 个 index 对象全部是 BASE TABLE |

`pg_class` 另有 325 个非表/视图关系：267 个 INDEX、56 个 SEQUENCE、2 个 COMPOSITE TYPE；这些不进入 `167 - 115` 的对象差异。精确关系为：`167 - 115 = 52 = 47 个未索引 BASE TABLE + 5 个未索引 VIEW`。旧报告的“47 张缺失表”只计算 BASE TABLE；52 与 47 的 5 项差异就是 5 个 VIEW，不是额外缺失表。

### 5.3 DB 表/视图不在 Index 的 52 个对象

- **staging 表（43 个，均为 BASE TABLE）**：`_stg_yichang_river_counts`、`_stg_yichang_river_import`、`_stg_yichang_river_std`、`stg_sjtj_sxhysjzxpt_dbo_operator_day_burnup_df`、`stg_sjtj_sxhysjzxpt_t_vessel_info_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_anchorage_clean_company_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_anchorage_info_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_clean_company_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_coordination_index_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_day_pollution_weight_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_day_receive_ship_kpi_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_day_subject_num_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_day_sxhy_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_month_receive_ship_kpi_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_order_receive_minute_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_process_unit_info_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_receive_ship_assessment_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_receive_ship_info_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_transfer_car_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_wharf_info_df`、`stg_sjtysj_cbwrwxtzlxxxt_b_wharf_kpi_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_apply_detail_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_ashore_apply_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_handover_trans_detail_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_handover_trans_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_receive_storage_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_trans_apply_detail_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_trans_apply_df`、`stg_sjtysj_cbwrwxtzlxxxt_p_wharf_storage_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_att_bas_base_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_att_wmst_base_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_rcm_rv_lk_res_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_rel_st_source_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_warn_stcd_r_df`、`stg_sslhhpj_hbsxkqycszhslzhglptjsxm_wr_mp_b_df`、`stg_ycsjtysj_sxhyzssj_base_channel_df`、`stg_ycsjtysj_sxhyzssj_base_channel_level_df`、`stg_ycsjtysj_sxhyzssj_base_ship_df`、`stg_ycsjtysj_sxhyzssj_data_coverage_wharf_df`、`stg_ycsjtysj_sxhyzssj_data_ship_info_df`、`stg_ycssthjj_ltwsjgpt_t_sjzx_wry_jbxx_df`、`stg_ycssthjj_lysthjjkyjpt_t_sjzx_szzdjcz_df`、`stg_ycssthjj_wryzxjkpt_t_zxjc_s_w_1_df`。
- **备份表（3 个，均为 BASE TABLE）**：`layer_river_provincial_bak0617`、`rs_industrial_info_yc_bak0305`、`wm_water_source_bak0421`。
- **系统扩展表（1 个，BASE TABLE）**：`spatial_ref_sys`。
- **普通 VIEW（5 个）**：`geography_columns`、`geometry_columns`、`v_wst_trace_edge_downstream`、`v_wst_trace_edge_upstream_conservative`、`v_wst_trace_edge_upstream_strict`。
- **业务正式表、materialized view、其他表/视图对象**：0 个。

这些对象应按 NL2SQL 允许范围分别评估，不能统称为“缺失表”。staging、备份和系统扩展表通常不应直接开放；5 个 VIEW 是否允许查询需按业务与安全规则决定。

### 5.4 在 Index 中但不在数据库的表

**0 张。** Index 中所有 115 张表均在数据库中存在。

### 5.5 Core 6 表字段对比

| 表名 | DB 字段数 | Index 字段数 | 差异 |
|------|---------|-------------|------|
| rs_outlet | 与 Index 一致 | 105 | 0 |
| rs_outlet_info_v2 | 与 Index 一致 | — | 0 |
| rs_outlet_monitor_v2 | 与 Index 一致 | — | 0 |
| rs_outlet_live_v2 | 与 Index 一致 | — | 0 |
| rs_outlet_trace_v2 | 与 Index 一致 | — | 0 |
| rs_outlet_remediation_v2 | 与 Index 一致 | — | 0 |

验证方式: 通过 `information_schema.columns` 逐表对比，未发现字段差异。

### 5.6 `metadata_view` — 纠错

Index 中包含 `metadata_view`（9 个字段），但本次只读查询确认其在当前数据库中是 **BASE TABLE**，不是 VIEW。Index 中属于 VIEW 的对象数量为 0。

---

## 6. 数据库权限与安全

### 6.1 数据库用户

- **当前用户**: `postgres`
- **超级用户**: **是** (`usesuper = true`)
- **默认事务只读**: **否** (`default_transaction_read_only = off`)
- **Statement Timeout**: **0** (无超时限制)
- **所有数据库用户**: 仅 `postgres` 一个用户

### 6.2 Schema Public 权限

用户 `postgres` 在 `public` schema 上拥有以下权限:
- `SELECT` ✅ (必需)
- `INSERT` ⚠️ (不应授予 NL2SQL 应用)
- `UPDATE` ⚠️ (不应授予)
- `DELETE` ⚠️ (不应授予)
- `TRUNCATE` ⚠️ (不应授予)
- `REFERENCES` ⚠️
- `TRIGGER` ⚠️

### 6.3 专用只读账号

**不存在。** 数据库中仅有一个用户 `postgres` (超级用户)。未创建任何只读用户。

### 6.4 密码硬编码

**是。** 数据库密码 `test123456` 硬编码在 `agent_config.py` 第 22 行:

```python
DB_KWARGS = dict(
    host="localhost", port=5433, database="gt_monitor",
    user="postgres", password="test123456",
)
```

该文件被 Git 跟踪（不在 `.gitignore` 中），密码已暴露在版本历史中。

### 6.5 安全风险评估

| 风险 | 严重程度 | 说明 |
|------|---------|------|
| 超级用户 | **高** | NL2SQL 应用使用超级用户连接数据库 |
| 写权限 | **高** | 应用账号拥有 INSERT/UPDATE/DELETE/TRUNCATE 权限 |
| 无超时 | **中** | statement_timeout=0，恶意或错误查询可无限运行 |
| 密码硬编码 | **高** | 密码明文存储在 Git 跟踪文件中 |
| 无只读账号 | **高** | 无法通过数据库层面限制写操作 |

---

## 7. 测试和 CI 状态

### 7.1 后端测试

后端测试文件位于 `tools/test_*.py`（11 个文件）。静态审查确认：11 个都不连接数据库、不调用真实 LLM、不打开 Chroma、不执行 DDL/DML；其中 3 个使用内存 `FakeMemory`，其余为纯静态或 fake tool/context。每个脚本都会重写 `tools/*_test_result.md`，故全部在仓库外的基础提交克隆中执行。

共同环境：`VANNA_DATA_DIR=E:\3\_audit_temp\repository_audit_correction_20260714_102311\backend_runtime_vanna_data`，`AGENT_DATA_DIR=E:\3\_audit_temp\repository_audit_correction_20260714_102311\backend_runtime_agent_data`，`OPENCODE_API_KEY` 为空。命令列以临时克隆为工作目录，Python 为 `E:\3\posgresql\1\vanna_venv\Scripts\python.exe`。

| 文件 / 命令 `python tools/<file>` | 分类 | 退出码 | 主摘要通过/失败 | DB | Memory | 临时目录变化 |
|---|---|---:|---:|---|---|---|
| `test_guarded_run_sql_tool.py` | 纯静态 + fake inner tool | 0 | 15/0 | 否 | 否 | 仅重写临时报告 |
| `test_metadata_context_enhancer.py` | 纯静态 + fake context | 0 | 8/0 | 否 | 否 | 仅重写临时报告 |
| `test_metadata_retriever.py` | 纯静态 | 0 | 20/0 | 否 | 否 | 仅重写临时报告 |
| `test_metadata_retriever_level3_p1.py` | 纯静态 | 0 | 23/0；另嵌套既有回归 20/0 | 否 | 否 | 仅重写临时报告 |
| `test_metadata_retriever_level3_p2.py` | 纯静态 | 0 | 9/0；另嵌套 P1 回归 23/0 | 否 | 否 | 仅重写临时报告 |
| `test_sql_example_context_enhancer.py` | 仅内存 FakeMemory | 1 | 16/1 | 否 | 否（非 Chroma） | 仅重写临时报告 |
| `test_sql_example_context_integration.py` | 纯静态源码集成检查 | 0 | 6/0 | 否 | 否 | 仅重写临时报告 |
| `test_sql_example_context_p1.py` | 仅内存 FakeMemory | 1 | 12/2 | 否 | 否（非 Chroma） | 仅重写临时报告 |
| `test_sql_example_context_p2.py` | 仅内存 FakeMemory | 0 | 16/0 | 否 | 否（非 Chroma） | 仅重写临时报告 |
| `test_sql_guard.py` | 纯静态 | 0 | 31/0 | 否 | 否 | 仅重写临时报告 |
| `test_sql_guard_execution_chain.py` | 纯静态 + fake inner tool | 0 | 7/0 | 否 | 否 | 仅重写临时报告 |

执行汇总：11/11 文件执行；9 个文件退出码 0、2 个退出码 1。按每个脚本主摘要合计 **163 通过、3 失败**；两个嵌套回归另执行 43 个通过用例，若按所有实际函数调用累计则为 206/3。3 个失败均是过期断言：旧测试把当前已允许的 P1 或 P2 `training_level` 当作“未知”，以及期望白名单精确等于 L2/P0/P1；当前实现白名单已合法包含 P2。未执行文件：无。

首次共用临时克隆时，两个带 `git status` 门禁的文件因前序测试已修改临时报告而提前退出；随后各自在全新干净克隆中重跑，上表记录重跑结果。四个受保护临时数据目录前后逐文件哈希完全一致。

### 7.2 前端测试

本次补证只针对指定的 6 个文件。静态检查结果：均未导入 Vitest，也没有注册 Vitest 的 `test()`、`it()` 或 `describe()`；其中 5 个定义自己的顶层 `test()`，`shadowComparisonV2.test.ts` 直接执行顶层断言。准确性质为：**自执行验证脚本，被 Vitest 加载但没有注册测试套件**。`--reporter=verbose` 只改变输出格式，不改变 Vitest 的测试发现逻辑。

当前 `frontend/package.json` 未声明 `vitest`、`vite-node` 或 `tsx`。本次使用仓库外 `run_vite_selftest.mjs` 调用项目已安装的 Vite `createServer().ssrLoadModule()`，缓存目录也在仓库外。逐文件命令为：

```powershell
node E:\3\_audit_temp\repository_audit_correction_20260714_102311\run_vite_selftest.mjs "/src/__tests__/<文件名>"
```

| 文件 | Vitest `test/it` | 顶层自执行 | 退出码 | 通过/失败 |
|------|------------------|------------|-------:|----------:|
| `chatStorageSlimming.test.ts` | 否 | 是 | 0 | 44/0 |
| `chartAppendDowngrade.test.ts` | 否 | 是 | 0 | 48/0 |
| `chartViewSpecPreservation.test.ts` | 否 | 是 | 0 | 18/0 |
| `shadowComparisonV2.test.ts` | 否 | 是 | 0 | 25/0 |
| `sourceDataPreservation.test.ts` | 否 | 是 | 0 | 5/0 |
| `sseChartDataframeProtection.test.ts` | 否 | 是 | 0 | 14/0 |
| **合计** | **标准 Vitest 文件 0** | **自执行文件 6** | **全部 0** | **154/0** |

### 7.3 CI/CD

**不存在。** 无 `.github/workflows/` 目录，无 CI 配置文件。

### 7.4 测试不会自动阻止提交

无 Git hooks、无 CI pipeline、无 `npm test` 脚本。`package.json` 仅含 `dev`/`build`/`lint`/`preview` 四个脚本。

---

## 8. 旧脚本及文档漂移

### 8.1 旧脚本列表

| 脚本 | 问题 | 风险 |
|------|------|------|
| `step4_agent.py` | 绕过 SQLGuard, 确定性元数据, SQL 示例; 使用旧 LLM 网关 | 如被误用, SQL 直接执行无校验 |
| `step4_test2.py` | 同上 + 无 OptimizedSystemPromptBuilder | 同上 + 无规则约束 |
| `diag_tool_calls.py` | 同上 | 同上 |

### 8.2 文档漂移清单

#### CLAUDE.md

| 项目 | 文档声称 | 实际值 | 严重程度 |
|------|--------|-------|---------|
| LLM 网关 | `https://opencode.ai/zen/go/v1` | `https://api.deepseek.com` | **高** |
| LLM Key 环境变量 | `OPENCODE_API_KEY` | `DEEPSEEK_API_KEY` | **高** |
| 架构图 (Agent 组成) | 不含 GuardedRunSqlTool/SQLGuard/DeterministicMetadataEnhancer/SqlExampleContextEnhancer | 当前主链路含全部 | **高** |
| 数据库表数 | "约 107 张" | 实际 162 张 BASE TABLE | 中 |
| 向量库内容 | "6 张表的 DDL + 2 条示例问答" | 6 DDL + 2 旧式示例 + 64 条 approved SQL 示例 | **高** |
| "关键脚本" 表 | step4_agent.py 标注为当前 CLI 快速验证 | step4_agent.py 实际已过时(用旧 API)，不应作为当前验证入口 | 中 |

#### docs/ARCHITECTURE.md

| 项目 | 文档声称 | 实际值 | 严重程度 |
|------|--------|-------|---------|
| LLM 网关 | `https://opencode.ai/zen/go/v1` | `https://api.deepseek.com` | **高** |
| Agent 架构图 | 不含 Guard 和确定性增强器 | 当前含三层层叠 Enhancer + SQLGuard | **高** |
| VisualizeDataTool | "已注册" | 当前主服务未注册 | 中 |

#### docs/RUNBOOK.md

| 项目 | 文档声称 | 实际值 | 严重程度 |
|------|--------|-------|---------|
| LLM 网关 | `https://opencode.ai/zen/go/v1` | `https://api.deepseek.com` | **高** |
| LLM Key | `OPENCODE_API_KEY` | `DEEPSEEK_API_KEY` | **高** |
| 启动命令 | `$env:OPENCODE_API_KEY = "sk-你的key"` 后 `python step4_server.py` | 应设置 `$env:DEEPSEEK_API_KEY` | **高** |

#### docs/PROJECT_STATUS.md

| 项目 | 文档声称 | 实际值 | 严重程度 |
|------|--------|-------|---------|
| 表数量 | "约 107 张" | 162 张 | 中 |
| 向量库内容 | "6 张表的 DDL + 2 条示例问答" | + 64 条 approved SQL | **高** |

#### docs/TROUBLESHOOTING.md

基本准确，但未提及:
- SQLGuard 阻塞诊断方法
- SqlExampleContextEnhancer 检索失败排查
- metadata index 过期排查

#### .env.example

| 项目 | 文件声称 | 实际 |
|------|--------|------|
| 内容 | `DEEPSEEK_API_KEY=your_deepseek_api_key_here` | ✅ 正确 (与 step4_server.py 匹配) |

#### AGENTS.md

在 `.gitignore` 中被忽略 (`AGENTS.md` 行)。仓库中不存在此文件。

---

## 9. 已确认问题

### 9.1 安全问题 (Critical)

1. **数据库超级用户**: `postgres` 超级用户用于 NL2SQL 应用 (`agent_config.py:21-22`)
2. **密码硬编码**: `test123456` 明文存储在 Git 跟踪文件中 (`agent_config.py:22`)
3. **无只读账号**: 无法通过数据库层面限制写操作
4. **Statement Timeout 为 0**: 无查询超时保护

### 9.2 架构问题

5. **旧脚本绕过安全体系**: `step4_agent.py`, `step4_test2.py`, `diag_tool_calls.py` 不使用 SQLGuard/确定性元数据/SQL 示例增强器
6. **文档全面过时**: 4/5 文档包含错误的 LLM 网关、API Key 名称、架构描述
7. **无 CI/CD**: 无自动化测试、无 lint 检查、无构建验证

### 9.3 数据问题

8. **column_metadata_index.json 无生成脚本**: 115 表索引文件来源不明，无法重新生成
9. **ChromaDB 仅覆盖 6 表 DDL**: 115 表有 metadata index 但仅 6 表有 DDL 在 ChromaDB 中
10. **52 个 DB 表/视图对象不在 metadata index**: 43 张 staging 表、3 张备份表、1 张 PostGIS 扩展表、5 个普通 view；不能全部称为“缺失表”

### 9.4 依赖问题

11. **无正式依赖文件**: 无 requirements.txt，无法复现环境
12. **Vanna editable install 依赖外部 Git 仓库**: `git+https://github.com/adso123456/1.git@89eb4d75` — 仓库删除或不可达将导致无法安装

---

## 10. 无法确认的问题

1. **正式 Chroma 三个既存修改文件在变化前的精确逻辑内容** — 未找到审计前副本，因此不能证明二进制变化前后绝对一致，也不能证明变化仅发生在索引。
2. **column_metadata_index.json 的生成方式** — 全仓搜索未发现生成脚本；可能来自外部工具或已删除脚本，但没有证据确认。
3. **NL2SQL 最终允许对象清单** — 43 张 staging 表、3 张备份表、1 张系统扩展表和 5 个 view 是否需要开放，需业务和安全负责人确认。
4. **P1/P2 过期测试的处置方式** — 已确认 3 个失败断言与当前允许 P1/P2 的实现不一致，但本阶段只审计文档，未修改测试。
5. **前端 6 个自执行脚本是否迁移为标准 Vitest** — 当前性质和独立执行结果已确认；是否迁移属于后续工程决策。

---

## 11. 后续训练前必须解决的事项

1. **[CRITICAL] 创建专用只读数据库账号**，修改 `agent_config.py` 使用只读账号
2. **[CRITICAL] 移除 agent_config.py 中的硬编码密码**，改用环境变量 `DB_PASSWORD`
3. **[HIGH] 更新所有文档**: CLAUDE.md, ARCHITECTURE.md, RUNBOOK.md, PROJECT_STATUS.md — LLM 网关、API Key、架构链路
4. **[HIGH] 为 column_metadata_index.json 编写生成/更新脚本**，确保数据库结构变更后可重新生成
5. **[MEDIUM] 编写 requirements.txt 或 pyproject.toml**，锁定所有依赖版本
6. **[MEDIUM] 淘汰或更新旧脚本**: 删除或更新 `step4_agent.py`, `step4_test2.py`, `diag_tool_calls.py`
7. **[MEDIUM] 设置 statement_timeout** (如 30s)，防止失控查询
8. **[MEDIUM] 设置默认事务只读**: `ALTER USER <readonly_user> SET default_transaction_read_only = on`

---

## 12. 训练规划建议 (不执行)

### 12.1 当前训练覆盖分析

| 维度 | 当前覆盖 | 目标 | 差距 |
|------|---------|------|------|
| ChromaDB DDL (Level 1) | 6 表 | 经批准的 NL2SQL 允许表 | 需先确定允许范围，不能用 115 个 index 对象机械计算差距 |
| SQL 示例 (Level 2) | 16 条 (水质+排污口) | — | 覆盖面可扩展 |
| SQL 示例 (Level 3 P0) | 18 条 (水质详细查询) | — | — |
| SQL 示例 (Level 3 P1) | 21 条 (排污口+废水+设备) | — | — |
| SQL 示例 (Level 3 P2) | 9 条 (多表 JOIN) | — | 覆盖面可扩展 |

### 12.2 建议训练顺序

1. **先解决安全问题** → 创建只读账号，定义数据库层和 SQLGuard 共用的 NL2SQL 允许表清单。
2. **划分对象范围** → 明确排除内部、staging、备份、系统扩展表；对 5 个 view 单独评估，不把 115 个 metadata 对象视为默认允许表。
3. **以实际依赖和近期业务定优先级** → 64 条 approved 示例当前依赖 21 张表：`gis_region_county`、4 张排污口相关表、3 张废水记录表、`wm_camera_info`、`wm_camera_platform`、`wm_hydrological_info`、`wm_section_info`、`wm_section_wq_info`、`wm_uav_info`、`wm_water_intake`、`wm_water_source`、`wm_waterbody_info`、4 张水质记录表。先核对这些表与预计近期业务范围。
4. **补充 Level 1 Text Memory** → 只为已批准查询且属于实际业务域的表生成带中文注释、排除 geometry 的 DDL；训练前后做备份、逻辑清单和回归验证。
5. **再扩展 Level 2/3 SQL 示例** → 仅覆盖已批准业务领域，并修正当前 3 个过期测试断言后纳入统一测试入口。

### 12.3 训练风险提示

- `train_step3.py` 使用 `information_schema.columns` 查询并执行 SQL，当前使用超级用户账号。如该脚本被意外修改，可能执行非预期 SQL。
- 训练脚本直接写入生产 ChromaDB，无备份机制。建议训练前备份 `vanna_data/` 目录。
- `memory.save_tool_usage()` 写入 ChromaDB 后无法单独回滚某条记录，错误数据需手动通过 ChromaDB API 删除。

---

## 附录 A: 执行过的核查命令

```bash
# Git 状态
git rev-parse HEAD && git rev-parse --abbrev-ref HEAD && git status --short

# 文件树
find . -not -path './.git/*' -not -path './node_modules/*' ...

# 数据指纹
find vanna_data -type f -exec md5sum {} \; | sort -k2 | md5sum
find agent_data -type f -exec md5sum {} \; | sort -k2 | md5sum

# Python 环境
vanna_venv/Scripts/python.exe --version
vanna_venv/Scripts/pip.exe freeze
vanna_venv/Scripts/pip.exe show vanna

# ChromaDB 分析
# - 正式目录仅做普通文件字节指纹
# - 基线与当前目录均经两次复制后，才用 Chroma API 读取二次副本
# - 比较 collection、record ID、document/metadata 哈希、embedding 和 metadata 分布

# 数据库分析
# - information_schema.tables 表计数
# - pg_user 用户权限查询
# - SHOW default_transaction_read_only / statement_timeout

# 后端测试：仓库外克隆 + 临时 VANNA_DATA_DIR/AGENT_DATA_DIR
# 前端 6 个自执行脚本：仓库外 Vite SSR 模块加载器逐文件执行

# 元数据索引来源搜索
grep -rn "column_metadata_index" --include="*.py" --include="*.md" .
git log --oneline --follow -10 -- agent_data/column_metadata_index.json
```

## 附录 B: 最重要的五项审计结论

1. **数据库安全严重不足**: 超级用户 + 硬编码密码 + 无只读账号 + 无超时限制。这是部署前必须解决的核心风险。

2. **文档全面过时**: CLAUDE.md、ARCHITECTURE.md、RUNBOOK.md、PROJECT_STATUS.md 所描述的 LLM 网关 (`opencode.ai`)、API Key (`OPENCODE_API_KEY`)、Agent 架构均与当前 `step4_server.py` 实际代码不符。按文档操作将导致启动失败。

3. **Chroma 基线与当前逻辑内容不一致**: 当前比最近可信备份多 9 条 P2 Tool Memory，record ID、document 哈希、metadata 哈希均不同；缺少精确审计前基线，不能证明既存二进制变化仅影响索引。

4. **64 条 approved Tool Memory 经内容级去重无重复**: 64 个 sample_id、question_hash、sql_hash 和组合哈希均唯一；编号空缺都有 review_result 或训练报告证据。

5. **测试现状已补证但仍有维护缺口**: 后端 11/11 文件实际执行，主摘要 163/3，3 个失败来自 P1/P2 白名单扩展后的过期断言；前端指定 6 个文件是自执行脚本，独立执行 154/0，并非标准 Vitest 套件。
