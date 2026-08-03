# 项目状态

> 更新基线：`dbde2c1`。本文描述当前主链路，不替代正式 Catalog 和运行资产中的实时状态。

## 当前能力

| 模块 | 状态 |
| --- | --- |
| 多数据源管理 | PostgreSQL、MySQL 通过 Catalog 独立管理，支持发现、训练、发布、启停和 revision 校验 |
| 数据源运行时 | `DataSourceRuntimeManager` 按 `source_id + runtime_revision` 缓存并热切换；服务启动时顺序预热可问数数据源 |
| 智能问数 | 会话强绑定数据源，SQLGuard 按方言校验，只读查询返回真实 DataFrame、文本和图表 |
| 性能与流式 | 简单查询快速路径、精简上下文、阶段进度事件、增量文本输出、取消和超时边界已接入 |
| 报表 | 日报/月报先确认配置，再查询数据并生成预览及 PDF；报表资产与普通问数隔离 |
| 仪表板 | 支持创建、删除和保存图表卡片 |
| 小助手 | 支持应用管理、来源与数据源授权、关联网站、外观、跨域父页面代理和 Widget |
| 问题建议 | 从各数据源离线生成并验证的问题资产中，按服务端会话绑定和 runtime revision 安全读取；同一会话确定性展示，不跨数据源补齐 |
| 前端 | React + TypeScript + Vite，主工作台与 Widget 共用后端契约 |

## 正式资产边界

- Catalog 只记录数据源生命周期、选定范围和运行 revision，不在代码清理中改写。
- PostgreSQL 与 MySQL 的 Metadata、Memory、Chroma 和报表产物是运行资产，不属于可随意删除的源码缓存。
- 数据源凭据只通过环境变量或凭据存储提供，不写入代码、文档、测试报告或 Git。
- 历史审计文档会明确标记为快照；它们只用于追溯，不能当作当前运行手册。

## 维护原则

1. 数据源结构变化必须走发现、训练、发布流程，不直接编辑 SQLite 或 Chroma。
2. runtime revision 由发布流程管理，不通过代码修改或清理任务人为改变。
3. 本地测试报告写入 `.local/test-reports/`，不再提交运行输出快照。
4. 当前启动和排障方法分别以 `docs/RUNBOOK.md`、`docs/TROUBLESHOOTING.md` 为准。
