# 问数链路性能与真实流式优化证据

## 范围与方法

- 正式基线：`dc94c37717c6d1d69e622a6503741f36848a7c12`。
- 性能前测在 `f8921ee3fbb1755672989b8f3b9e44ccc0881167` 执行；该提交到正式基线之间，问数 Runtime、Agent、LLM、SQLGuard、Memory 与上下文代码无差异，唯一相关文件差异是 `step4_server.py` 的 R2 报表路由抽取，不进入本次直接 Handler 性能题组。
- PostgreSQL/MySQL 均使用只读连接、Catalog 副本、Metadata 副本、Memory/Chroma 副本和独立 trace 目录。未启动或重启正式 `8000/5173` 服务。
- 固定题组共 14 题：每个数据源各 3 个简单查询、2 个聚合/排名、1 个图表、1 个追问。
- 原始前测：`E:\3\_perf_runs\query-latency-optimization-v1\baseline-f8921ee\baseline-real-validation.json`，SHA256 `caa7f30d5b0336701e333f0f9c52dfda612ef0c3af1c3d30eaa242ced8dc9ad9`。
- 原始后测：`E:\3\_perf_runs\query-latency-optimization-v1\optimized-dc94c37\optimized-validation.json`，SHA256 `e25b3b5b57b20fb683a9cd04592262e5b379a5a6a44edea6c3ed04457ffd5c4b`。
- 准确率回退复测：`E:\3\_perf_runs\query-latency-optimization-v1\optimized-dc94c37\accuracy-fallback-validation.json`，SHA256 `26ce9b9c40d3b2f5b172a9e704eff2171acd1074ef1d42e19b52a66eb74da4fa`。

## 修改前后结果

| 指标 | 修改前 | 修改后 | 结果 |
| --- | ---: | ---: | --- |
| 14 题平均总耗时 | 17158.9 ms | 9176.3 ms | 降低 46.5% |
| 14 题中位总耗时 | 12128.1 ms | 5773.5 ms | 降低 52.4% |
| 14 题平均首段文本 | 17156.4 ms | 8713.0 ms | 降低 49.2% |
| 14 题平均 Provider 调用 | 3.86 | 2.00 | 降低 48.2% |
| 5 个稳定简单查询平均总耗时 | 10673.4 ms | 4059.4 ms | 降低 62.0% |
| 5 个稳定简单查询 Provider 调用 | 均为 2 次 | 均为 1 次 | 达标 |
| 5 个稳定简单查询 DataFrame 成功 | 5/5 | 5/5 | 无下降 |
| SIMPLE_LOOKUP 受控上下文长度 | 3134 字符 | 2139 字符 | 降低 31.75% |

后测是在共享模型服务存在波动的真实环境中完成，因此耗时用于体验级比较，不作为微基准。结构性验收由离线 Fake Provider 合同测试固定。

## Runtime 与首包

- 修改前冷构建：MySQL `10275.964 ms`，PostgreSQL `105.742 ms`。
- 修改后启动顺序预热：总计 `10563 ms`；MySQL `10328 ms`，PostgreSQL `110 ms`。
- 预热后请求全部命中同 revision Runtime，没有二次构建；PostgreSQL acquire 为 `31–47 ms`，MySQL为 `109–125 ms`。
- 第一条 `progress`：PostgreSQL `46–47 ms`，MySQL `125 ms`，均低于 `500 ms`。

## 真流式、并发与取消

- MySQL 聚合题在完整回答结束前分别收到 `251` 和 `158` 个 `text_delta`；首段正文分别为 `8281 ms` 和 `5344 ms`，总耗时分别为 `12000 ms` 和 `8047 ms`。
- Fake Provider 两个慢请求并发完成约 `93–94 ms`，串行对照约 `187–188 ms`。
- 真实 PostgreSQL/MySQL 并发请求分别耗时 `4907 ms`、`8015 ms`，并发总耗时 `8078 ms`，接近较慢请求而非二者之和；期间事件循环探针为 `125 ms`。
- 取消合同验证：取消一个请求后并发槽立即可被下一请求取得，下一请求正常完成；`CancelledError` 不转换为成功。
- Provider 连接错误仅在尚未输出任何 chunk 时重试一次；Fake Provider 验证调用 2 次、项目重试计数 1。SDK 自带重试固定为 0，避免双重重试。

## 正确性回退

- 初次后测发现“排污口类型”和“排污口监测记录”使用缩减上下文时输出字段少于基线，因此把这些准确率敏感类型确定性回退到 `FULL`。
- 回退复测中，“排污口名称和类型”恢复 `catalog_level_1/2/3`；“最近排污口监测记录”恢复基线全部字段并额外返回 `water_temp`，均首次 SQL 成功、单次 Provider、保留 DataFrame。
- “水质监测断面名称”在前后测均未成功生成可执行 SQL；修改前调用 8 次、约 `39806 ms`，修改后由 Tool 轮上限在 3 次停止、约 `20984 ms`。该题不计入快速路径成功率，也未通过缩减上下文掩盖失败。
- 聚合、趋势、排名、图表、报表、复杂追问、多结果和语义不明确问题始终使用 `FULL`；SQL 修正、Guard warning、Tool 异常、Provider 重试、多 DataFrame 或超过 50 行均禁止快速路径。

## Vanna 2.0.2 与 OpenAI 客户端结论

- Vanna `Agent._handle_streaming_response()` 消费 `llm_service.stream_request()` 的全部 chunk 并累积成一个 `LlmResponse`，源码注释明确留下 “Could yield intermediate TextChunk here”，这是最终正文在 Agent 层被完整缓冲的实际位置。
- Vanna `OpenAILlmService` 在 `async` 方法中创建同步 `OpenAI` 客户端，并用同步 `for event in stream` 迭代，因而会阻塞事件循环。
- 项目侧兼容服务改用原生 `AsyncOpenAI`，继续复用 Vanna 的 payload 构造、Tool Call 协议和项目既有 DeepSeek/tool_choice/thinking/trace 策略；没有修改 `site-packages`，也没有复制 Agent。
- 项目侧通过请求事件队列旁路 Vanna 的最终正文缓冲，只在 answer-only 轮转发 `text_delta`；Tool Call 草稿、reasoning、Tool JSON 和系统提示词不会进入用户事件。

## 正式资产边界

- 正式 Catalog 只读复核：PostgreSQL `ready + enabled, revision 1`；MySQL `ready + enabled, revision 3`。
- 正式 Metadata 与基线副本逐字节哈希一致：PostgreSQL `29748be455746cb469fd1d525f232cf681c341b8643bcd76ef076f865c1134df`；MySQL `53aff3b06d5f0604f5f2a24db67c5f35339837b329d032713d55cc84998709e1`。
- 所有性能运行只打开隔离 Memory/Chroma 副本；没有 discover、prepare、publish、enable、disable、训练或 revision 变更。
- 正式 `5173` 进程在本任务开始前已存在，未重启；正式 `8000` 未监听，本任务未启动它。

## 基线既有失败

- `tools/test_guarded_run_sql_tool.py` 在干净的 `dc94c37` 上为 `14/15`，既有失败为 `candidate mismatch warning 不阻断`；性能分支结果相同。
- `tools/test_data_source_request_coordinator.py` 在干净的 `dc94c37` 上为 `23/25`，既有失败是持久化绑定错误消息不含测试期待的 source id，以及并发跨源失败消息同一合同；性能分支结果相同。
- `tools/test_sql_example_context_enhancer.py` 为 `21/22`：生产白名单已有 6 个合法 level（包含 MySQL），但基线测试仍硬编码“5 个精确值”；其余检索、过滤、top_k 与 EXACT QUESTION MATCH 21 项全部通过。
- 以上均为正式基线既有合同不一致，未作为本性能任务顺手修改。
