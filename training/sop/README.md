# 训练批次静态审查器

本目录实现阶段 0B-1 的版本化训练批次契约和纯静态审查。它属于项目治理工具，不是 Vanna 官方强制流程，也不会训练、连接数据库、打开 Chroma 或写入 Memory。

主要模块：

- `batch_schema.py`：Pydantic 2 批次模型，禁止未知字段和隐式类型转换。
- `batch_validator.py`：复用项目 `SQLGuard`，检查批次契约、单语句和表清单，并计算确定性 SHA-256。
- `tools/validate_training_batch.py`：命令行入口；默认只输出到终端。

`batch_content_sha256` 覆盖通过校验的完整批次契约：顶层字段、按原顺序排列的全部样本及其工具参数、审查信息和预期行为。普通文本去除首尾空白，SQL 规范化外部空白和末尾单个分号，预期表去除 schema 前缀后转小写、排序。摘要不包含时间、绝对路径、运行环境或随机值，无效批次不生成正式摘要。

示例：

```powershell
python tools/validate_training_batch.py tools/fixtures/training_sop/valid_batch.json
```

如需 JSON 或 Markdown 结果，输出路径必须位于仓库外的临时或交付目录，且不得指向任何 `vanna_data` 或 `agent_data` 目录。

## 0B-2A：训练数据目录指纹与验证副本

`storage_snapshot.py` 提供纯文件系统能力：生成包含文件、隐藏文件和空目录的确定性清单；在源目录复制前后检查稳定性；将副本先写入目标同级临时目录，完成逐文件和整体摘要验证后再发布；以及从已验证备份复制到全新目录进行恢复副本演练。CLI 为：

```powershell
python tools/snapshot_training_store.py manifest <source>
python tools/snapshot_training_store.py backup <source> <new-destination>
python tools/snapshot_training_store.py verify <source> <copy>
python tools/snapshot_training_store.py restore-rehearsal <source> <backup> <new-restore-destination>
```

所有源和目标路径必须显式提供。工具不会搜索或默认使用 `vanna_data`、`agent_data` 或 Chroma 路径；验证副本和恢复目录必须位于项目仓库之外且原本不存在。它不导入 Chroma、SQLite 数据库客户端、数据库驱动或 Memory API。

最终目录使用平台原子 `no-replace` 发布语义；目标在发布前任何时刻出现，操作都会失败且绝不覆盖。平台缺少已实现的可靠 `no-replace` 原语时失败关闭，不降级为可能覆盖目标的普通重命名。恢复演练在源、备份和临时恢复目录三方验证全部完成前不会发布最终恢复目录。

该工具通过复制前后源目录指纹检测变化，但它不是数据库在线热备份协议。正式 T4 执行时仍必须先停止或阻断所有 Chroma 写入，确认服务和训练进程不会修改正式数据目录，再进行文件级备份。不得声称或假设该工具能在正式 Chroma 持续写入时保证事务一致性。
