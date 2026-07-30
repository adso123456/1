# B5 动态数据源管理与会话强绑定

## 1. 总体设计

B5 将 `config/data_sources.py` 降为首次迁移的 bootstrap。运行时事实源是 SQLite 动态目录，默认位于 `agent_data/data_sources/catalog.sqlite3`，可用 `DATA_SOURCE_CATALOG_PATH` 覆盖。运行数据库、WAL、候选目录、新建数据源资产和本地 `.env` 均由 Git 忽略。

目录、Runtime、Metadata、Memory 和会话均以不可变 `source_id` 路由。`display_name` 只负责用户界面展示，重命名不会移动资产、改变 Widget 授权或修改历史绑定。

## 2. 数据模型与迁移

schema 组件名为 `data_source_catalog`，当前版本为 5。`system_schema_versions` 记录版本；初始化使用 `BEGIN IMMEDIATE`、WAL、外键和 busy timeout。v1→v2 迁移增加 MySQL TLS 模式和证书路径字段；v3 增加不含凭据的 `pending_asset_cleanup`；v4 增加 `active_asset_batches`；v5 将批次扩展为完整快照、资产计划、逐项进度、owner、更新时间和错误摘要。旧目录可幂等升级并保持默认非 TLS 行为。

`data_sources` 保存身份与显示信息、数据库类型与连接模式、生命周期、连接参数、凭据模式、资产路径、运行时 revision、发现结果、选择范围、路由摘要、明确能力和审计状态。`conversation_source_bindings` 以 `conversation_id` 为主键，外键指向 `source_id`。`pending_asset_cleanup` 只保存受管资产清理重试信息；`active_asset_batches` 是跨 Preparer 实例和进程边界的活动批次保护登记，不保存凭据。

首次启动以 `INSERT OR IGNORE` 迁移 `postgresql-main` 和 `mysql-lzh-monitor`。两者继续引用既有 Metadata/Chroma 路径，凭据只保存环境变量名；重复启动不会覆盖用户修改的显示名称。

## 3. 生命周期

```text
draft → connected → metadata_ready → training_required → ready ↔ disabled
```

连接、发现或 Runtime 阻断错误进入 `error`。状态只能由后端动作推进。启停严格限定为 `ready → disabled` 和 `disabled → ready`，并要求 revision 与正式 Metadata/Memory 存在；目录层在同一 SQLite 写事务中按当前状态条件更新。`draft/connected/metadata_ready/training_required/error` 均拒绝启停。

配置变更要求复测；连接测试成功进入 `connected`，既有 `ready/disabled` 成功复测不降级。`ready/disabled` 保存新范围后强制进入 `training_required` 并关闭问数；不能借 `disable/enable` 绕过。只有重新完成候选验证、原子发布、revision 递增和新 Runtime 构建，才能恢复 `ready`。停用保留正式资产与历史绑定，但拒绝新绑定和新请求。

## 4. 凭据安全

内置源使用 `credential_mode=environment`，只保存用户名/密码环境变量名。新建源使用 Fernet 对称加密，主密钥来自 `DATA_SOURCE_CREDENTIAL_KEY`。首次本机启动缺少密钥时，会生成到 Git 忽略的 `.env`；日志和终端不输出密钥。管理 API 只返回 `has_password` 和用户名状态，不返回明文或密文。密码留空表示保持原值。

## 5. Connector 与只读边界

首版 `DirectDatabaseConnector` 支持 PostgreSQL 和 MySQL。`external_provider` 只保留连接模式扩展点，不提供 UI 或虚构协议。

连接测试验证握手、认证、数据库、版本查询、只读事务和 Metadata 读取，始终回滚并关闭连接。错误映射为连接超时、认证失败、数据库不存在、SSL 错误或权限不足，不返回 DSN、底层堆栈和凭据。

发现只使用 `information_schema` 和 PostgreSQL 系统注释函数；查询参数固定，前端不能提交 SQL。PostgreSQL 自动排除 geometry。

MySQL TLS 支持 `disabled / required / verify_ca / verify_identity`。CA、客户端证书和私钥只保存本机路径，证书与私钥必须成对；CA 验证模式要求文件存在，身份验证不能静默降级。测试连接和正式 `ReadOnlyMySQLRunner` 都使用同一个 TLS 参数构造器。PostgreSQL 继续使用原有 `sslmode`。

索引发现从 MySQL `information_schema.statistics` 和 PostgreSQL `pg_index/pg_class/pg_namespace/pg_attribute/pg_am` 读取，保留唯一、主键、方法、全部字段、字段顺序和方向。DDL 生成先从整张表的发现元数据按索引名汇总完整索引：`PRIMARY KEY` 只允许来源于字段完整、顺序明确且无表达式的 `primary=true` 索引对象；普通和唯一索引也必须完整落入选择范围。`primary_key=true` 仅表示主键成员，绝不作为生成回退。旧目录若存在主键成员标记但没有完整主键索引，prepare 会拒绝并要求重新读取表和字段；无主键旧表仍可正常生成。复合键部分选择不会缩短成伪键，顺序始终使用数据库真实顺序。

## 6. 范围、训练和发布

范围项必须来自本次发现结果，至少包含一张表。未选择或排除字段不会进入 Metadata、DDL、路由摘要和 SQLGuard 白名单。

准备流程按 `source_id` 生成方言正确的 Metadata、每表一条确定性 DDL Memory、每表一条基于真实注释的基础文档 Memory和安全路由摘要。不自动编造 SQL Tool Memory。同一 `source_id` 使用所有 Preparer 实例共享的进程内锁并明确拒绝重复 prepare，不同数据源可以并行；SQLite 活动批次唯一登记提供多进程重复发布门禁。prepare 开始时记录 runtime revision、状态和选择范围确定性哈希，文件安装前复查，并由 `catalog.publish` 在同一写事务内再次做乐观校验，变化时拒绝旧批次发布。

候选 Chroma 在隔离目录写入并校验计数。Metadata、Memory、DDL、业务文档、asset manifest 和 Catalog 发布字段作为同一协调发布单元；任一步失败均按持久化状态机恢复完整旧版或完整新版。Memory 使用 revision 版本路径发布，使 Windows 下仍被旧 Runtime 持有的 Chroma 不阻塞新版本切换；旧请求继续使用旧目录，新请求按新 revision 获取新目录。

清理提交点位于候选完整验证、五类资产安装、Catalog 发布、revision 更新以及新 Runtime 成功构建和缓存切换之后。当前 Catalog 五类正式资产、当前/retired/关闭失败 Runtime Memory 和全部活动批次路径均进入保护集合。Runtime 释放回调只重试 `pending_asset_cleanup` 明确登记的路径，不扫描目录；成功 prepare 才在批次协调保护内扫描本源过期资产。启动时依据 owner 存活、阶段、快照、manifest 和实际文件执行恢复，不再直接删除活动批次。目录外路径、内置 B3 资产、B4 报表和其他数据源永不进入清理范围。

## 7. Runtime 失效规则

`DataSourceRuntimeManager` 的缓存键为 `source_id + runtime_revision`。prepare 发布新 revision 后不先 invalidate：管理器保留旧缓存，构建并验证新 Runtime，成功后才在锁内原子替换 Runtime 与 revision。旧实例有租约时进入 retired，无租约时立即安全关闭；最后一个租约释放且关闭成功后才触发明确 pending 清理。关闭失败的 Runtime 继续保护其 Memory 并等待重试。新 Runtime factory 或校验失败时，缓存 Runtime/revision 不变，旧实例不关闭、不进入 retired、不触发释放回调；prepare 随后恢复 Catalog 和五类文件并删除新失败 revision，旧 Memory 完整保留。`disabled/error` 拒绝新请求。

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

## 12. 崩溃安全发布与恢复

目录 schema 当前为 v5。`active_asset_batches` 的 `source_id` 唯一约束同时承担跨进程发布门禁；批次保存 `owner_pid`、开始/更新时间、完整旧 Catalog 快照、目标 revision/Memory、五类资产计划、每项正式/候选/备份路径、旧版与目标哈希、已完成备份/安装集合、阶段及安全错误摘要，不保存凭据或连接串。

发布阶段为：

```text
prepared
  → backing_up
  → installing
  → catalog_published
  → runtime_validated
  → committed

任一未提交阶段失败
  → rolling_back
  → 完成后删除批次
  → 无法证明一致时 rollback_failed
```

每次文件移动后先把完成集合提交到 SQLite，再进行下一项。发布单元包含 `column_metadata_index.json`、revision Memory、`ddl_memories.json`、`business_documents.json`、`asset_manifest.json`、Catalog 发布字段和 Runtime 缓存。manifest 记录 source、revision、scope fingerprint、批次 ID、创建时间以及四类内容哈希；Memory 使用目录内不可变 `.asset_identity.json` 作为版本身份，避免 Chroma 运行时文件变化造成误判。

启动恢复不再把活动批次当作垃圾删除。owner 仍存活的批次始终返回冲突，避免长时间 Chroma 构建被其他实例误接管；owner 已退出的批次按实际 Catalog 状态恢复：

| 状态 | 恢复决策 |
|---|---|
| Catalog 仍是旧 revision | 按逐项备份记录恢复完整旧版 |
| Catalog 已是目标 revision | 校验 manifest 和全部目标哈希，再重建并验证 Runtime；成功则前滚提交 |
| 新 Runtime 无法重建 | 恢复旧 Catalog 与全部旧资产 |
| 路径越界、快照缺失或哈希无法证明 | 保留 batch/backup，标记 `rollback_failed`，关闭该源问数 |
| `runtime_validated` / `committed` 遗留 | 重新校验目标资产和 Runtime 后只做提交后清理 |

恢复操作幂等：备份已被移回时以旧哈希确认，目标已删除时直接继续；恢复中再次崩溃后，下次启动从现存文件和持久化集合继续。恢复前会重新推导并严格核对五类正式、候选和备份路径；任何空路径、目录越界或路径篡改都保留证据并进入 `rollback_failed`，不会执行清理。backup 只在完整回滚验证成功或完整新版验证成功后删除，active batch 是最后删除项之一。清理失败只登记精确 pending 路径，不撤销已提交发布。

Runtime factory 或 validate 失败不会修改旧缓存。新 Runtime 成功后，runtime 对象与 revision 在同一临界区替换；旧 Runtime 有租约时进入 retired，无租约时关闭。关闭失败的 Runtime 继续保护旧 Memory，只有重试关闭成功后才触发 release callback。候选 Memory 关闭仅停止自身 executor 并通过客户端引用计数释放，不调用 Chroma SharedSystem 的 `stop()` 或全局 cache clear；候选客户端释放失败会阻断发布并进入恢复，避免带锁继续安装。

并发修改范围或停用时，旧批次会在发布前乐观校验失败；回滚只撤销本批次拥有的发布字段，不覆盖并发产生的 `training_required` 或 `disabled` 状态。主键规则保持不变：仅完整、顺序明确的 `primary=true` 索引可以生成 `PRIMARY KEY`，字段级 `primary_key=true` 不能作为回退。

## 13. 已知限制

- 不支持外部接口、文件数据源、其他数据库、ETL、跨源 JOIN 和写入；
- 不提供关系图、手工 JOIN、行级权限或在线 SQL 示例编辑；
- 不定时刷新 Metadata；
- 新源首版只训练 DDL 与确定性基础文档；
- 本机 MySQL 未配置强制 TLS，TLS 验收覆盖参数构造和连接参数一致性，未声称完成真实云 TLS 握手；
- 前端 localStorage 的全部依赖无法由服务端独立枚举，因此物理删除同时采用后端硬门禁。
- `tools/test_sql_guard_execution_chain.py` 的既有 6/7 结果源于测试未固定 `deterministic_candidate_tables` 时 Retriever 召回的不确定性；该历史契约不属于 B5 回归，本次未修改 SQLGuard、Retriever 或 GuardedRunSqlTool。
