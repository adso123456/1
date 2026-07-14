# 仓库文件清单 (Repository Inventory)

> 审计日期: 2026-07-14
> 仓库: E:\3\posgresql\1
> 分支: master, HEAD: 89eb4d75fcaf5ac140a4ff90c31da82c8b7e7ffc

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

| 文件 | 测试对象 |
|------|---------|
| `tools/test_sql_guard.py` | SQLGuard |
| `tools/test_guarded_run_sql_tool.py` | GuardedRunSqlTool |
| `tools/test_metadata_retriever.py` | DeterministicMetadataRetriever |
| `tools/test_metadata_context_enhancer.py` | DeterministicMetadataContextEnhancer |
| `tools/test_sql_example_context_enhancer.py` | SqlExampleContextEnhancer |
| `tools/test_sql_example_context_integration.py` | SQL Example + Guard 集成 |
| `tools/test_sql_example_context_p1.py` | P1 级别测试 |
| `tools/test_sql_example_context_p2.py` | P2 级别测试 |
| `tools/test_sql_guard_execution_chain.py` | SQLGuard 执行链 |
| `tools/test_metadata_retriever_level3_p1.py` | Metadata Retriever P1 |
| `tools/test_metadata_retriever_level3_p2.py` | Metadata Retriever P2 |

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
| `chartCapabilityV2.test.ts` | 自定义验证脚本 | 使用 verbose reporter 时通过 (44/44) |
| `chartCapabilityResolverV2.test.ts` | 自定义验证脚本 | 通过 (107/107) |
| `chartDataTransformV2.test.ts` | 自定义验证脚本 | 通过 (43/43) |
| `chartPipelineV2.test.ts` | 自定义验证脚本 | 通过 (39/39) |
| `chartPlannerV2.test.ts` | 自定义验证脚本 | 通过 (16/16) |
| `chartGoldenFixturesV2.test.ts` | 自定义验证脚本 | 通过 (25/25) |
| `chartDescriptionV2.test.ts` | 自定义验证脚本 | 通过 (29/29) |
| `chartAvailabilityV2.test.ts` | 自定义验证脚本 | 通过 (20/20) |
| `chartSwitchMessageUpdate.test.ts` | 自定义验证脚本 | 通过 (10/10) |
| `dashboardChartSwitchV2.test.ts` | 自定义验证脚本 | 通过 (12/12) |
| `datasetProfilerV2.test.ts` | 自定义验证脚本 | 通过 (37/37) |
| `userSwitchV2.test.ts` | 自定义验证脚本 | 通过 (5/5) |
| `reTransformFromSource.test.ts` | 自定义验证脚本 | 通过 (4/4) |
| `runtimeChartPathV2.test.ts` | 自定义验证脚本 | 通过 (22/22) |
| `chatStorageSlimming.test.ts` | 自定义验证脚本 | 标准 vitest run 报 "No test suite found" |
| `chartAppendDowngrade.test.ts` | 自定义验证脚本 | 同上 |
| `chartViewSpecPreservation.test.ts` | 自定义验证脚本 | 同上 |
| `shadowComparisonV2.test.ts` | 自定义验证脚本 | 同上 |
| `sourceDataPreservation.test.ts` | 自定义验证脚本 | 同上 |
| `sseChartDataframeProtection.test.ts` | 自定义验证脚本 | 同上 |
| `goldenFixtures.ts` | 测试 fixtures (数据) | 非测试文件 |

**注意:** 所有前端测试文件使用自定义验证模式（直接打印结果），不是标准 vitest `test()`/`it()` 块。使用 `npx vitest run --reporter=verbose` 时可以执行并输出结果；标准 `npx vitest run` 会报 "No test suite found"。没有统一测试入口，不会自动阻止提交。

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
| `vanna_data/` | ChromaDB 持久化数据 | 1 collection (tool_memories), 72 embeddings: 8 text (Level 1), 64 tool usage approved |
| `agent_data/` | Agent 运行时数据 | 含 `column_metadata_index.json` (115 表, 2572 字段) |
| `capture/` | LLM 调用捕获日志 (4 个 .jsonl 文件) | 诊断用途 |

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
