# 仓库文件清单 (Repository Inventory)

> 审计日期: 2026-07-14
> 仓库: E:\3\posgresql\1
> 分支: master, 基础 HEAD: 42be3c0e59324f6fa0767ae69b45fa6962aa8729

---

## 1. Python 入口脚本

| 文件 | 职责 | 当前主链路 | 疑似遗留 | 测试覆盖 |
|------|------|-----------|---------|---------|
| `step4_server.py` | 主 FastAPI 服务入口 (:8000) | **是** | 否 | 无自动测试 |
| `step4_agent.py` | CLI 问答测试 (旧版) | 否 | **是** — 使用 OPENCODE_API_KEY + opencode.ai，绕过 SQLGuard/DeterministicMetadataEnhancer/SqlExampleContextEnhancer | 无 |
| `step4_test2.py` | CLI 问答测试输出到文件 (旧版) | 否 | **是** — 同上绕过 Guard；没有 system_prompt_builder (无 OptimizedSystemPromptBuilder) | 无 |
| `diag_tool_calls.py` | 诊断脚本 | 否 | **是** — 使用 OPENCODE_API_KEY + opencode.ai，绕过 Guard 体系，使用 RunSqlTool 直接执行 | 无 |
| `train_step3.py` | Level 1 训练: 提取 6 表 DDL + 示例问答存入 ChromaDB | 否 (训练脚本) | 否 | 无 |
| `agent_config.py` | 共享配置: DB 连接、ChromaDB 实例、embedding function | 是 (被 import) | 否 | 无 |

## 2. 后端核心模块 (`tools/`)

| 文件 | 职责 | 当前主链路 | 疑似遗留 | 测试覆盖 |
|------|------|-----------|---------|---------|
| `tools/sql_guard.py` | 基于 `column_metadata_index.json` 的 SQL 静态校验器 | **是** | 否 | `tools/test_sql_guard.py` |
| `tools/guarded_run_sql_tool.py` | 包装 RunSqlTool，执行前调用 SQLGuard | **是** | 否 | `tools/test_guarded_run_sql_tool.py` |
| `tools/metadata_retriever.py` | 确定性元数据检索器 (表/字段匹配+意图评分) | **是** | 否 | `tools/test_metadata_retriever.py` |
| `tools/metadata_context_enhancer.py` | 第2层 Context Enhancer: 追加确定性元数据候选 | **是** | 否 | `tools/test_metadata_context_enhancer.py` |
| `tools/sql_example_context_enhancer.py` | 第3层 Context Enhancer: 注入 approved SQL 示例 | **是** | 否 | `tools/test_sql_example_context_enhancer.py` |

## 3. 诊断和探针工具 (`tools/`)

| 文件 | 职责 | 当前主链路 |
|------|------|-----------|
| `tools/check_deepseek_config.py` | 检查 DeepSeek 配置一致性 | 辅助 |
| `tools/check_sql_examples_level2.py` | 检查 Level 2 SQL 示例 | 辅助 |
| `tools/diagnose_l2_example_recall.py` | 诊断 Level 2 示例召回问题 | 辅助 |
| `tools/diagnose_llm_context_injection.py` | 诊断 LLM 上下文注入 | 辅助 |
| `tools/e2e_minimal_probe.py` | 端到端最小探针 | 辅助 |
| `tools/full_levels1_3_regression.py` | Level 1-3 全量回归 | 辅助 |
| `tools/live_service_manual_probe.py` | 在线服务手动探针 | 辅助 |
| `tools/metadata_retriever_integration_probe.py` | 元数据检索器集成探针 | 辅助 |
| `tools/reg_q2_annual_waterquality_probe.py` | 年度水质回归探针 | 辅助 |
| `tools/sql_example_context_full_validation_probe.py` | SQL 示例全量验证 | 辅助 |
| `tools/sql_example_context_integration_probe.py` | SQL 示例集成探针 | 辅助 |
| `tools/audit_level1_chroma_coverage.py` | Level 1 Chroma 覆盖审计 | 辅助 |
| `tools/level2_post_training_probe.py` | Level 2 训练后探针 | 辅助 |
| `tools/level3_p0_post_training_probe.py` | Level 3 P0 训练后探针 | 辅助 |
| `tools/level3_p0_q7_block_diagnostic.py` | Level 3 P0 Q7 阻塞诊断 | 辅助 |
| `tools/level3_p1_post_training_probe.py` | Level 3 P1 训练后探针 | 辅助 |
| `tools/level3_p2_post_training_probe.py` | Level 3 P2 训练后探针 | 辅助 |

## 4. 工具测试文件 (`tools/`)

11 个文件均在仓库外克隆中实际执行；共同设置仓库外 `VANNA_DATA_DIR`、`AGENT_DATA_DIR`，未连接数据库、未调用真实 LLM、未打开 Chroma、未执行 DDL/DML。脚本只重写临时克隆中的结果报告。

| 文件 | 测试对象 / 分类 | 退出码 | 主摘要通过/失败 |
|------|-----------------|------:|----------------:|
| `tools/test_sql_guard.py` | SQLGuard；纯静态 | 0 | 31/0 |
| `tools/test_guarded_run_sql_tool.py` | GuardedRunSqlTool；fake inner tool | 0 | 15/0 |
| `tools/test_metadata_retriever.py` | DeterministicMetadataRetriever；纯静态 | 0 | 20/0 |
| `tools/test_metadata_context_enhancer.py` | DeterministicMetadataContextEnhancer；fake context | 0 | 8/0 |
| `tools/test_sql_example_context_enhancer.py` | SqlExampleContextEnhancer；内存 FakeMemory | 1 | 16/1 |
| `tools/test_sql_example_context_integration.py` | SQL Example + Guard 源码集成检查 | 0 | 6/0 |
| `tools/test_sql_example_context_p1.py` | P1；内存 FakeMemory | 1 | 12/2 |
| `tools/test_sql_example_context_p2.py` | P2；内存 FakeMemory | 0 | 16/0 |
| `tools/test_sql_guard_execution_chain.py` | SQLGuard 执行链；fake inner tool | 0 | 7/0 |
| `tools/test_metadata_retriever_level3_p1.py` | Metadata Retriever P1；纯静态 | 0 | 23/0，另嵌套回归 20/0 |
| `tools/test_metadata_retriever_level3_p2.py` | Metadata Retriever P2；纯静态 | 0 | 9/0，另嵌套回归 23/0 |

主摘要合计 163/3，9 个文件退出码 0、2 个文件退出码 1；嵌套回归另有 43/0。3 个失败均来自旧测试把当前已允许的 P1/P2 `training_level` 当作未知值，或仍要求白名单精确等于 L2/P0/P1。未执行文件为 0。

## 5. 训练脚本 (`training/`)

| 文件 | 职责 | 修改 ChromaDB |
|------|------|-------------|
| `training/train_sql_examples_level2.py` | Level 2 训练写入 (16 条) | **是** |
| `training/train_sql_examples_level3_p0.py` | Level 3 P0 训练写入 (18 条) | **是** |
| `training/train_sql_examples_level3_p1.py` | Level 3 P1 训练写入 (21 条) | **是** |
| `training/train_sql_examples_level3_p2.py` | Level 3 P2 训练写入 (9 条) | **是** |
| `training/check_sql_examples_level3_p0.py` | Level 3 P0 前置检查 | 否 |
| `training/check_sql_examples_level3_p1.py` | Level 3 P1 前置检查 | 否 |
| `training/check_sql_examples_level3_p2.py` | Level 3 P2 前置检查 | 否 |
| `training/level3_p0_pretrain_validation.py` | Level 3 P0 预训练验证 | 否 |
| `training/review_sql_examples_level3_p1.py` | Level 3 P1 SQL 审查 | 否 |
| `training/review_sql_examples_level3_p2.py` | Level 3 P2 SQL 审查 | 否 |
| `training/validate_sql_examples_level3_p1_pretraining.py` | Level 3 P1 预训练验证 | 否 |
| `training/validate_sql_examples_level3_p2_pretraining.py` | Level 3 P2 预训练验证 | 否 |
| `training/audit_level3_p2_join_feasibility.py` | Level 3 P2 JOIN 可行性审计 | 否 |

## 6. 训练相关 JSON 和 Markdown (`training/`)

| 文件 | 职责 |
|------|------|
| `training/sql_examples_level2_draft.json` | Level 2 草稿 |
| `training/sql_examples_level3_p0_draft.json` | Level 3 P0 草稿 |
| `training/sql_examples_level3_p0_review_result.json` | Level 3 P0 审查结果 |
| `training/sql_examples_level3_p1_draft.json` | Level 3 P1 草稿 |
| `training/sql_examples_level3_p1_review_result.json` | Level 3 P1 审查结果 |
| `training/sql_examples_level3_p2_draft.json` | Level 3 P2 草稿 |
| `training/sql_examples_level3_p2_review_result.json` | Level 3 P2 审查结果 |
| 各种 `*_result.md`, `*_review.md`, `*_plan.md`, `*_scope.md`, `*_check.md` | 训练报告和计划文档 |

编号空缺均有草案和 review/训练证据，不是“原因未知”：

- L2 冻结：`L2_SQL_011`、`L2_SQL_012` 因业务字段口径未确认；`L2_SQL_019` 因 SQLGuard warning、表不在 deterministic candidate tables 且场景稳定性待确认；decision 均为 `requires_manual_review`。
- P1 冻结：`L3_P1_SQL_004`、`L3_P1_SQL_005`、`L3_P1_SQL_010` 的状态字段没有允许值、示例值或枚举证据，无法确认固定值“是”；decision 均为 `requires_manual_review`。
- P2 排除：`L3_P2_SQL_009`、`L3_P2_SQL_010` 的 J5 精确匹配为 0，4 位城市码不能与 6 位区县码精确 JOIN；decision 均为 `excluded`。

## 7. 文档 (`docs/`)

| 文件 | 职责 | 过时 |
|------|------|------|
| `docs/ARCHITECTURE.md` | 架构文档 | **是** — 见审计证据文档 |
| `docs/PROJECT_STATUS.md` | 项目状态 | **是** — 见审计证据文档 |
| `docs/RUNBOOK.md` | 运行手册 | **是** — 见审计证据文档 |
| `docs/TROUBLESHOOTING.md` | 排障记录 | 基本准确 |

## 8. 前端源码 (`frontend/src/`)

| 文件 | 职责 |
|------|------|
| `frontend/src/App.tsx` | React 主应用组件 |
| `frontend/src/main.tsx` | React 入口 |
| `frontend/src/types.ts` | TypeScript 类型定义 |
| `frontend/src/chartCapabilityResolverV2.ts` | 图表能力解析器 V2 |
| `frontend/src/chartCapabilityV2.ts` | 图表能力 V2 |
| `frontend/src/chartDataTransformV2.ts` | 图表数据转换 V2 |
| `frontend/src/chartDescription.ts` | 图表描述 |
| `frontend/src/chartPipelineV2.ts` | 图表管线 V2 |
| `frontend/src/chartPlannerV2.ts` | 图表规划器 V2 |
| `frontend/src/chartRegistry.ts` | 图表注册表 |
| `frontend/src/chartSemantics.ts` | 图表语义 |
| `frontend/src/datasetProfilerV2.ts` | 数据集分析器 V2 |
| `frontend/src/components/AddChartDialog.tsx` | 添加图表对话框 |
| `frontend/src/components/AddToDashboardDialog.tsx` | 添加到仪表板对话框 |
| `frontend/src/components/ChartCard.tsx` | 图表卡片 |
| `frontend/src/components/ChartView.tsx` | 图表视图 |
| `frontend/src/components/ChatArea.tsx` | 聊天区域 |
| `frontend/src/components/DashboardListPanel.tsx` | 仪表板列表面板 |
| `frontend/src/components/DashboardView.tsx` | 仪表板视图 |
| `frontend/src/components/MessageBubble.tsx` | 消息气泡 |
| `frontend/src/components/Sidebar.tsx` | 侧边栏 |
| `frontend/src/components/TableView.tsx` | 表格视图 |
| `frontend/src/components/ThinkingSteps.tsx` | 思考步骤 |
| `frontend/src/hooks/useDashboard.ts` | 仪表板 Hook |
| `frontend/src/hooks/useSSE.ts` | SSE Hook |
| `frontend/src/utils/dashboardExport.ts` | 仪表板导出 |
| `frontend/src/utils/tableFormatting.ts` | 表格格式化 |

## 9. 前端测试文件 (`frontend/src/__tests__/`)

| 文件 | 类型 | 实际状态 |
|------|------|---------|
| `chartCapabilityV2.test.ts` | 自定义验证脚本 | 上一阶段记录 44/44，本次未复跑 |
| `chartCapabilityResolverV2.test.ts` | 自定义验证脚本 | 上一阶段记录 107/107，本次未复跑 |
| `chartDataTransformV2.test.ts` | 自定义验证脚本 | 上一阶段记录 43/43，本次未复跑 |
| `chartPipelineV2.test.ts` | 自定义验证脚本 | 上一阶段记录 39/39，本次未复跑 |
| `chartPlannerV2.test.ts` | 自定义验证脚本 | 上一阶段记录 16/16，本次未复跑 |
| `chartGoldenFixturesV2.test.ts` | 自定义验证脚本 | 上一阶段记录 25/25，本次未复跑 |
| `chartDescriptionV2.test.ts` | 自定义验证脚本 | 上一阶段记录 29/29，本次未复跑 |
| `chartAvailabilityV2.test.ts` | 自定义验证脚本 | 上一阶段记录 20/20，本次未复跑 |
| `chartSwitchMessageUpdate.test.ts` | 自定义验证脚本 | 上一阶段记录 10/10，本次未复跑 |
| `dashboardChartSwitchV2.test.ts` | 自定义验证脚本 | 上一阶段记录 12/12，本次未复跑 |
| `datasetProfilerV2.test.ts` | 自定义验证脚本 | 上一阶段记录 37/37，本次未复跑 |
| `userSwitchV2.test.ts` | 自定义验证脚本 | 上一阶段记录 5/5，本次未复跑 |
| `reTransformFromSource.test.ts` | 自定义验证脚本 | 上一阶段记录 4/4，本次未复跑 |
| `runtimeChartPathV2.test.ts` | 自定义验证脚本 | 上一阶段记录 22/22，本次未复跑 |
| `chatStorageSlimming.test.ts` | 非标准自执行验证脚本 | 本次独立执行 44/0，退出码 0 |
| `chartAppendDowngrade.test.ts` | 非标准自执行验证脚本 | 本次独立执行 48/0，退出码 0 |
| `chartViewSpecPreservation.test.ts` | 非标准自执行验证脚本 | 本次独立执行 18/0，退出码 0 |
| `shadowComparisonV2.test.ts` | 非标准自执行验证脚本 | 本次独立执行 25/0，退出码 0 |
| `sourceDataPreservation.test.ts` | 非标准自执行验证脚本 | 本次独立执行 5/0，退出码 0 |
| `sseChartDataframeProtection.test.ts` | 非标准自执行验证脚本 | 本次独立执行 14/0，退出码 0 |
| `goldenFixtures.ts` | 测试 fixtures (数据) | 非测试文件 |

**本次指定 6 个文件的准确性质:** 未导入 Vitest，也未注册 Vitest `test()`/`it()`/`describe()`；5 个定义自己的顶层 `test()`，1 个直接执行顶层断言。它们是“自执行验证脚本，被 Vitest 加载但没有注册测试套件”。`--reporter=verbose` 只改变输出格式，不改变测试发现。当前 `package.json` 未声明 `vitest`、`vite-node` 或 `tsx`；本次通过仓库外 Vite SSR 模块加载器逐文件执行，共 154/0。

## 10. 前端配置文件

| 文件 | 职责 |
|------|------|
| `frontend/package.json` | 依赖和脚本 (dev/build/lint/preview) |
| `frontend/package-lock.json` | 依赖锁定 |
| `frontend/vite.config.ts` | Vite 配置 (含 proxy → :8000) |
| `frontend/tsconfig.json` | TypeScript 配置 |
| `frontend/tsconfig.app.json` | App 级别 TS 配置 |
| `frontend/tsconfig.node.json` | Node 级别 TS 配置 |
| `frontend/.oxlintrc.json` | Oxlint 配置 |
| `frontend/index.html` | HTML 入口 |
| `frontend/.gitignore` | 前端 Git 忽略 |

## 11. 后端配置文件

| 文件 | 职责 |
|------|------|
| `.env.example` | 环境变量示例 (DEEPSEEK_API_KEY, VANNA_DATA_DIR, AGENT_DATA_DIR) |
| `.gitignore` | Git 忽略 (含 AGENTS.md) |
| `CLAUDE.md` | 项目说明文档 |

## 12. 数据目录

| 目录 | 内容 | 当前状态 |
|------|------|---------|
| `vanna_data/` | ChromaDB 持久化数据 | 当前二次副本：1 collection，72 embeddings（8 text + 64 approved tool usage）；最近可信备份为 63 条，逻辑内容不一致 |
| `agent_data/` | Agent 运行时数据 | 含 `column_metadata_index.json` (115 表, 2572 字段) |
| `capture/` | LLM 调用捕获日志 (4 个 .jsonl 文件) | 诊断用途 |

Chroma 内容级补证：当前相对可信备份新增 9 条 P2 Tool Memory；record ID 集、document 哈希、metadata 哈希均不一致。没有精确审计前副本，不能证明正式二进制变化前后逻辑内容绝对一致。当前 64 条 Tool Memory 的 sample_id、规范化 question、规范化 SQL、question+SQL 组合哈希均为 64 个唯一值，四类重复组均为 0。

正式目录本阶段清单指纹（相对路径、长度、单文件 SHA-256 的规范化清单再哈希）：`vanna_data/` 开始/结束均为 `83fe3fb3c7b735f3b665a8105a8e8d705f801c1da467dd9274292ce732ec1ee5`；`agent_data/` 开始/结束均为 `c0d4ecf8733ce08121e7b19f59a2d96fece2fbb2d19a72f898cc387515a49a73`。两者本阶段均未变更。

### 12.1 数据库对象与 Metadata Index

| 集合 | 数量 | 关系 |
|------|-----:|------|
| `public` BASE TABLE | 162 | 其中 47 个不在 index |
| `public` VIEW | 5 | 5 个均不在 index |
| `public` MATERIALIZED VIEW | 0 | 无 |
| 表/视图合计 | 167 | `167 - 115 = 52` |
| Metadata Index 对象 | 115 | 全部是 BASE TABLE；index - DB 为 0 |

52 个 DB-index 差异对象分类为 43 张 staging 表、3 张备份表、1 张 PostGIS 系统扩展表、5 个普通 view；业务正式表、materialized view 和其他表/视图为 0。`metadata_view` 在当前数据库中实际是 BASE TABLE，不是 VIEW。完整 52 个名称和类型见 `docs/repository_audit_evidence.md` 第 5.3 节。

## 13. Vanna 源码 (`vanna_src/`)

完整 Vanna 2.0.2 源码，通过 editable pip install 安装。包含:
- `src/vanna/core/` — 核心模块 (Agent, Enhancer, Tool, User, LLM, Storage, 等)
- `src/vanna/integrations/` — 集成 (ChromaDB, PostgreSQL, OpenAI, Plotly, 等)
- `src/vanna/servers/` — 服务器 (FastAPI, Flask, CLI)
- `src/vanna/tools/` — 内置工具 (RunSql, VisualizeData, FileSystem)
- `src/vanna/legacy/` — 遗留 Vanna 0.x API

## 14. CI 配置

**不存在。** 仓库无 `.github/workflows/` 目录，无 CI/CD 配置文件。无统一测试入口脚本。

## 15. 启动脚本

无独立启动脚本（`.sh`, `.ps1`, `.bat`）。启动方式见 `CLAUDE.md` 和 `docs/RUNBOOK.md`，需手动激活 venv 并执行 `python step4_server.py`。

## 16. 审计边界与后续训练范围

- 无法确认：正式 Chroma 3 个既存修改文件在变化前的精确逻辑内容、metadata index 的生成方式、NL2SQL 最终允许对象清单，以及是否把 6 个前端自执行脚本迁移为标准 Vitest。
- Level 1 Text Memory 不应机械覆盖 115 个 metadata 对象。应先确定 NL2SQL 允许表，排除内部/staging/备份/系统对象，并单独评估 view。
- 64 条 approved 示例实际依赖 21 张表；应结合这 21 张表与近期业务范围确定补充 DDL 和示例的优先级，再进行受控训练和回归。

## 统计汇总

| 分类 | 数量 |
|------|------|
| Python 入口脚本 | 6 |
| 后端核心模块 (tools/) | 5 |
| 诊断/探针工具 (tools/) | 18 |
| 工具测试文件 (tools/) | 11 |
| 训练脚本 (training/) | 12 |
| 训练数据/报告 (training/) | ~25 |
| 文档 (docs/) | 4 |
| 前端源码 (src/) | 29 |
| 前端测试文件 (__tests__/) | 21 |
| 配置文件 | 11 |
| **总计 (不含 venv/node_modules/vanna_src)** | **~150** |
