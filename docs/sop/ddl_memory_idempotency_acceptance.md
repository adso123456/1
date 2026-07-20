# DDL Memory 115 条全量隔离幂等验收 Runbook

## 1. 适用范围

本 Runbook 仅用于 F6-1F：使用当前真实 Metadata 生成的全部 115 条 DDL，在仓库外全新隔离 Chroma 中验证两轮受控 Apply 的幂等性。它不是正式资产切换、清理或治理 SOP。

禁止使用正式 Chroma `E:\3\_runtime\vanna-level1\vanna_data`、仓库内 `vanna_data`、旧 `save_text_memory()`、随机 UUID Memory ID、`collection.delete()` 或任何数据库连接。失败后必须废弃整个候选运行目录，不得在原目录重试。

## 2. 前置基线

在仓库 `E:\3\posgresql\1` 执行：

```powershell
git fetch origin
git rev-parse HEAD
git rev-parse origin/master
git status --short
```

本阶段基线为：

```text
4eadb6070de84c4028c846f65d3406dedd6124bf
```

HEAD 或 `origin/master` 不等于基线时立即停止。记录既有脏状态，不清理、不修改、不暂存受保护文件。

## 3. 路径格式

每次运行必须使用新的时间戳和全新目录：

```text
E:\3\_training_backups\f6-1f-<YYYYMMDD-HHMMSS>\isolated_chroma
E:\3\_training_backups\f6-1f-<YYYYMMDD-HHMMSS>\evidence
```

两个目录必须属于同一个运行根目录。脚本会在创建 Client 前执行 F6-1D 隔离门禁；正式路径、仓库路径、非规定目录或非空目录均失败。

## 4. 唯一允许的命令

先执行无 Client 自检：

```powershell
python -m training.sop.ddl_memory_idempotency_acceptance --self-test
```

再使用全新时间戳执行一次完整验收：

```powershell
python -m training.sop.ddl_memory_idempotency_acceptance `
  --acceptance-test `
  --isolated-chroma E:\3\_training_backups\f6-1f-<时间戳>\isolated_chroma `
  --evidence-dir E:\3\_training_backups\f6-1f-<时间戳>\evidence
```

脚本只调用 `train_step3.load_metadata_index()`、`group_tables()` 和 `build_all_table_ddls()` 构造真实输入。必须先确认 Metadata 非空、表/DDL/唯一表名/唯一 logical ID/唯一 record ID 均为 115，随后才允许创建隔离 Client。

## 5. PASS 计数

第一轮 Plan：

```text
desired=115
managed_existing=0
unmanaged_existing=0
create=115
unchanged=0
changed=0
removed=0
```

第一轮 Apply：

```text
created=115
verified_noop=0
replaced=0
retained_removed=0
count_before=0
count_after=115
```

关闭并重开后，第二轮 Plan：

```text
desired=115
managed_existing=115
unmanaged_existing=0
create=0
unchanged=115
changed=0
removed=0
```

第二轮 Apply：

```text
created=0
verified_noop=115
replaced=0
retained_removed=0
count_before=115
count_after=115
```

再次关闭并重开后，总数和 managed 数必须为 115，unmanaged 为 0，三类重复组必须全部为 0，最终 Plan 仍为 115 个 unchanged。

## 6. 语义快照

语义快照按 `record_id` 排序，每条只纳入 `record_id`、DDL document SHA-256 和稳定排序后的 managed Metadata。它不包含 embedding、运行时间或绝对路径，也不把完整 DDL 写入 Evidence。

以下三个输出必须完全一致：

```text
FIRST_APPLY_SNAPSHOT_SHA
SECOND_APPLY_SNAPSHOT_SHA
REOPENED_FINAL_SNAPSHOT_SHA
```

同时必须输出 `SNAPSHOT_EQUALITY_TEST=PASS`。

## 7. PASS/FAIL 与失败处置

只有输入门禁、两轮 Plan/Apply、两次关闭重开、最终计数、三类重复检查、三次快照一致性全部通过，脚本才输出：

```text
ddl_memory_idempotency_acceptance=PASS
```

任一条件不满足即为 FAIL，退出码非 0。失败候选目录视为不可验收：保留 Evidence 供审计后废弃整个 `f6-1f-<时间戳>` 目录；禁止原地继续 Apply、修补、删除记录或作为下一次验收输入。

本 Runbook 不授权打开或治理正式 Chroma，不授权新增正式 Memory，不测试 Top-K 检索，不进入 F6-1G。
