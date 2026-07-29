# B5 动态数据源管理与会话强绑定

## 1. 总体设计

B5 将 `config/data_sources.py` 降为首次迁移的 bootstrap。运行时事实源是 SQLite 动态目录，默认位于 `agent_data/data_sources/catalog.sqlite3`，可用 `DATA_SOURCE_CATALOG_PATH` 覆盖。运行数据库、WAL、候选目录、新建数据源资产和本地 `.env` 均由 Git 忽略。

目录、Runtime、Metadata、Memory 和会话均以不可变 `source_id` 路由。`display_name` 只负责用户界面展示，重命名不会移动资产、改变 Widget 授权或修改历史绑定。

## 2. 数据模型与迁移

schema 组件名为 `data_source_catalog`，当前版本为 2。`system_schema_versions` 记录版本；初始化使用 `BEGIN IMMEDIATE`、WAL、外键和 busy timeout。v1→v2 迁移增加 MySQL TLS 模式和证书路径字段，旧目录幂等升级并保持默认非 TLS 行为。

`data_sources` 保存身份与显示信息、数据库类型与连接模式、生命周期、连接参数、凭据模式、资产路径、运行时 revision、发现结果、选择范围、路由摘要、明确能力和审计状态。`conversation_source_bindings` 以 `conversation_id` 为主键，外键指向 `source_id`。

首次启动以 `INSERT OR IGNORE` 迁移 `postgresql-main` 和 `mysql-lzh-monitor`。两者继续引用既有 Metadata/Chroma 路径，凭据只保存环境变量名；重复启动不会覆盖用户修改的显示名称。

## 3. 生命周期

```text
draft → connected → metadata_ready → training_required → ready ↔ disabled
```

连接、发现或 Runtime 阻断错误进入 `error`。状态只能由后端动作推进。配置变更要求复测；连接测试成功进入 `connected`，既有 `ready/disabled` 成功复测不降级；保存范围后生成候选资产；候选验证和原子发布后进入 `ready` 并递增 revision；停用保留正式资产与历史绑定，但拒绝新绑定和新请求。

## 4. 凭据安全

内置源使用 `credential_mode=environment`，只保存用户名/密码环境变量名。新建源使用 Fernet 对称加密，主密钥来自 `DATA_SOURCE_CREDENTIAL_KEY`。首次本机启动缺少密钥时，会生成到 Git 忽略的 `.env`；日志和终端不输出密钥。管理 API 只返回 `has_password` 和用户名状态，不返回明文或密文。密码留空表示保持原值。

## 5. Connector 与只读边界

首版 `DirectDatabaseConnector` 支持 PostgreSQL 和 MySQL。`external_provider` 只保留连接模式扩展点，不提供 UI 或虚构协议。

连接测试验证握手、认证、数据库、版本查询、只读事务和 Metadata 读取，始终回滚并关闭连接。错误映射为连接超时、认证失败、数据库不存在、SSL 错误或权限不足，不返回 DSN、底层堆栈和凭据。

发现只使用 `information_schema` 和 PostgreSQL 系统注释函数；查询参数固定，前端不能提交 SQL。PostgreSQL 自动排除 geometry。

MySQL TLS 支持 `disabled / required / verify_ca / verify_identity`。CA、客户端证书和私钥只保存本机路径，证书与私钥必须成对；CA 验证模式要求文件存在，身份验证不能静默降级。测试连接和正式 `ReadOnlyMySQLRunner` 都使用同一个 TLS 参数构造器。PostgreSQL 继续使用原有 `sslmode`。

索引发现从 MySQL `information_schema.statistics` 和 PostgreSQL `pg_index/pg_class/pg_namespace/pg_attribute/pg_am` 读取，保留唯一、主键、方法、字段顺序和方向。表达式索引标记为不可直接表示，不伪造成普通字段索引。

## 6. 范围、训练和发布

范围项必须来自本次发现结果，至少包含一张表。未选择或排除字段不会进入 Metadata、DDL、路由摘要和 SQLGuard 白名单。

准备流程按 `source_id` 生成方言正确的 Metadata、每表一条确定性 DDL Memory、每表一条基于真实注释的基础文档 Memory和安全路由摘要。不自动编造 SQL Tool Memory。

候选 Chroma 在隔离目录写入并校验计数。Metadata、Memory、DDL、业务文档和 catalog 发布字段作为同一协调发布单元；任一步失败均补偿恢复全部旧文件、目录状态、路由摘要和 revision。Memory 使用 revision 版本路径发布，使 Windows 下仍被旧 Runtime 持有的 Chroma 不阻塞新版本切换；旧请求继续使用旧目录，新请求按新 revision 获取新目录。

## 7. Runtime 失效规则

`DataSourceRuntimeManager` 的缓存键为 `source_id + runtime_revision`。revision 变化后，下一个请求构建新 Runtime；构建成功才替换缓存。正在执行的请求继续持有旧对象，不使用候选半成品。`disabled/error` 拒绝新请求。

MySQL/PostgreSQL 继续使用既有各自 Runner、Metadata Retriever、Chroma、Prompt 和 SQLGuard，不跨源共享 Agent 或方言。

## 8. API

本地主工作台管理 API 复用小助手的 loopback 与精确同源边界：

- `GET/POST /api/data-source-management`
- `GET/PATCH/DELETE /api/data-source-management/{source_id}`
- `POST .../{source_id}/test-connection`
- `POST .../{source_id}/discover`
- `PUT .../{source_id}/scope`
- `POST .../{source_id}/prepare`
- `POST .../{source_id}/enable|disable`
- `GET .../{source_id}/dependencies`
- `POST/GET /api/conversations/{conversation_id}/source`

Widget 不能调用管理 API。`/api/embed/data-sources` 只返回 Token 授权且处于 `ready/enabled` 的安全摘要。

## 9. 会话强绑定

主工作台点击“新对话”只打开数据源选择弹窗，确认后才生成会话 ID并立即写入后端绑定。同源重复绑定幂等，改绑返回 409，停用或非 ready 源拒绝新绑定，清空消息不解除绑定。

升级前有消息但没有 `sourceId` 的会话不猜测归属，只读展示；用户可选择数据源复制为新会话。

主工作台由 `useSSE.refreshDataSources()` 维护共享安全摘要。管理页在创建、更新、测试、发现、保存范围、准备、启停和删除后主动刷新；新会话弹窗、标题、历史会话名称、建议卡和发送门禁立即使用最新值。发送必须同时满足已绑定、`status=ready`、`enabled_for_chat=true`，其他生命周期显示各自原因。

## 10. 错误源推荐

推荐在 Agent 前执行，不自动切换或跨源查询。优先精确匹配目录中注册的 capability，再对所有授权且 `ready/enabled` 的动态源使用安全路由摘要、表字段、注释、描述和业务别名确定性评分。普通推荐要求当前源得分不高于 4、候选至少 6 分且领先第二名至少 3 分；不确定时不推荐。普通评分不依赖固定 `source_id`。

结构化 `data_source_suggestion` 携带原问题和安全候选；点击后创建并绑定新会话，再重新发送原问题，原会话保持不变。

## 11. 停用、删除与回滚

停用是默认操作。内置源不可物理删除。删除前检查后端会话、Metadata、Memory、报表能力和内置标记；主工作台同时提交已知 localStorage 依赖。存在任何依赖时只允许停用。

目录回滚可停止服务后恢复 SQLite 备份；资产发布失败由代码自动恢复旧 Metadata/Memory。不得用回滚删除既有正式 Chroma。

## 12. 已知限制

- 不支持外部接口、文件数据源、其他数据库、ETL、跨源 JOIN 和写入；
- 不提供关系图、手工 JOIN、行级权限或在线 SQL 示例编辑；
- 不定时刷新 Metadata；
- 新源首版只训练 DDL 与确定性基础文档；
- 当前不会主动回收仍可能被旧 Runtime 持有的历史 Memory revision 目录；
- 本机 MySQL 未配置强制 TLS，TLS 验收覆盖参数构造和连接参数一致性，未声称完成真实云 TLS 握手；
- 前端 localStorage 的全部依赖无法由服务端独立枚举，因此物理删除同时采用后端硬门禁。
