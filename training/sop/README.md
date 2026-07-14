# 训练批次静态审查器

本目录实现阶段 0B-1 的版本化训练批次契约和纯静态审查。它属于项目治理工具，不是 Vanna 官方强制流程，也不会训练、连接数据库、打开 Chroma 或写入 Memory。

主要模块：

- `batch_schema.py`：Pydantic 2 批次模型，禁止未知字段和隐式类型转换。
- `batch_validator.py`：复用项目 `SQLGuard`，检查批次契约、单语句和表清单，并计算确定性 SHA-256。
- `tools/validate_training_batch.py`：命令行入口；默认只输出到终端。

示例：

```powershell
python tools/validate_training_batch.py tools/fixtures/training_sop/valid_batch.json
```

如需 JSON 或 Markdown 结果，输出路径必须位于仓库外的临时或交付目录，且不得指向任何 `vanna_data` 或 `agent_data` 目录。
