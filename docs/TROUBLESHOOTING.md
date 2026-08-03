# 排障手册

## 页面显示“请求失败（502）”

1. 先访问 `http://127.0.0.1:8000/health`。
2. 若无法访问，检查 8000 端口是否有后端进程，以及该进程是否从正确代码目录启动。
3. 若后端使用隔离端口，确认 Vite 代理指向同一端口。
4. 查看后端启动日志，优先处理缺失环境变量、Catalog 路径或依赖错误。

## 数据源列表为空或不可问数

- 检查 Catalog 中数据源是否同时为 `ready` 和 `enabled_for_chat`。
- 调用 `/api/runtime-prewarm-status` 查看 `not_started`、`warming`、`ready` 或 `failed` 状态。
- 对照当前 runtime revision 检查 Metadata、Memory 和 Chroma 正式资产是否存在。
- 不要直接编辑 SQLite、修改 revision 或重建 Chroma；需要修复时走正常管理流程。

## 首问慢或长时间停在“思考中”

- 查看 SSE 是否立即收到进度事件，以及卡在哪个阶段。
- 查看请求诊断中的 Runtime 获取、上下文检索、首次 LLM、SQLGuard、SQL 执行和最终文本耗时。
- 若 Runtime 预热失败，先处理对应数据源的连接或资产问题。
- 检查 `DEEPSEEK_API_KEY`、模型服务超时和重试日志，不要在日志中打印密钥。

## 查询结果不正确

- 确认会话绑定的数据源和页面显示的数据源一致。
- 确认本次请求产生了新的成功 `run_sql` 和 DataFrame，数据型追问不能复用上一轮结果冒充实时查询。
- 检查 Metadata、SQL 示例和检索上下文是否属于当前 source。
- 检查 SQLGuard 是否按 PostgreSQL/MySQL 的正确方言运行，并确认 SQL 排除了不应返回的大字段或空间字段。

## 前端取消后仍显示加载

- 确认请求触发 Abort 后，当前 assistant 消息的 `streaming` 已结束，进度文本和 request id 已清除。
- 若仍存在，查看浏览器控制台与 Network 中该 SSE 请求是否真的结束，并运行对应状态行为测试。

## 报表日期或内容异常

- 报表日期只是查询参数，不代表数据库一定有该日期数据。
- 生成前应由 options/校验链路确认可用时间范围；生成阶段必须以 SQL 查询结果为准，不能复用历史报告充当新数据。
- MySQL 时间范围使用左闭右开边界时，检查结束时间是否正确落到下一周期起点。

## 小助手或 Widget 无法访问

- 管理页为空时，确认 `WATER_AGENT_SYSTEM_DB_PATH` 指向预期系统数据库。
- 检查应用为 enabled，宿主页 Origin 精确包含在 `allowed_origins`，数据源在 `allowed_source_ids`。
- Embed 请求必须由宿主页 Loader 发起；缺失 Origin、未知应用和越权数据源都会被拒绝。
- 不要添加自定义“父 Origin”请求头，也不要恢复 Secret/JWT 链路。

## 安全操作原则

- 排障优先使用只读健康检查、日志和资产副本。
- 不删除正式 Catalog、Metadata、Memory、Chroma 或系统数据库。
- 不把密码、连接串、API Key 或应用 Secret 写入命令输出、文档和 Git。
