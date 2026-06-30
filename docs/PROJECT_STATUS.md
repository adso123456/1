# PROJECT_STATUS.md — 项目状态

## 当前数据范围

- 数据库共约 107 张表，白名单试点 6 张排污口表：
  - `rs_outlet` / `rs_outlet_info_v2` / `rs_outlet_monitor_v2`
  - `rs_outlet_live_v2` / `rs_outlet_trace_v2` / `rs_outlet_remediation_v2`
- 向量库存有：6 张表的 DDL + 中文注释 + 2 条示例问答

## 已完成功能

| 项目 | 状态 |
|------|------|
| 6 张表试点验证 | ✅ 通过（SQL 生成 + 数据查询 + 出图全链路） |
| 前端 | ✅ React + ECharts（`frontend/`，Vite + TypeScript）为当前主入口；FastAPI 根路径仍保留 Vanna 自带 `<vanna-chat>` 备用内置页面 |
| 图表链路 | ✅ React 通过 SSE 接收 `dataframe` 和 `text`，根据 `chart_type` 标记自行生成 ECharts option |
| VisualizeDataTool | ✅ 后端仍注册，可生成 Plotly ChartComponent；当前 React 前端不消费 Plotly 配置 |
| System Prompt 优化 | ✅ 禁止 schema 探查、禁止 SELECT geom、1-2 条 SQL 上限 |

## 待办事项和待定决策

| 项目 | 状态 |
|------|------|
| 白名单放开到全库 | 后续扩展 |
| 补充更多示例问答 | 后续扩展 |
