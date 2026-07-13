# Level 3 P2 approved SQL 示例受控写入结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- 当前 commit：17a22d1d54c53ea18c0a636f6ea296b73d907e24
- 初始 git status --short：
```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```
- 是否调用 vn.train()：否
- 是否调用 memory.save_tool_usage()：是
- 计划写入：9
- 成功：9
- 失败：0
- excluded 写入：0
- 备份目录：E:\3\_backup\level3_p2_training_20260713_164829
- 正式 agent_data/query_results 新增：0

## 写入前后指纹

- 写入前文件数：5
- 写入后文件数：5

### 写入前

- 68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=701d5e915735e6448bb699d09cb0a6fa4ee60e2f9cc0fa4d32b7ad5e2b41f6a7
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=78c470c832f4af1ce7b4bc0ba44d40d401462b248d6101a7b682feb8fe6e3c81
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- chroma.sqlite3 | size=761856 | sha256=179f687a03656e28a404a8405fb56868184123ced196f0f9503fcfb6b840241b

### 写入后

- 68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=79e24c98d12a58c5df056f34956bb5c51826c678efcd1385ce936dd7ff7da032
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=780435d24ea18dc238757b2284841f64867b9622d9e251157b6dc44b0569de0e
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- chroma.sqlite3 | size=864256 | sha256=cc59182a3440b9e77775d4e638358d43acafa0b0545755f43ad14e59d0c5805c

## 正式变化文件

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
- vanna_data/chroma.sqlite3

## 逐样本写入状态

| sample_id | question | status | error |
|---|---|---|---|
| L3_P2_SQL_001 | 查询排污口国家编码、名称及对应整治状态和整治类型记录明细 | success | 无 |
| L3_P2_SQL_002 | 按省市区县统计排污口总数和有整治记录的排污口数量，包含没有整治记录的排污口 | success | 无 |
| L3_P2_SQL_003 | 查询排污口国家编码、名称及对应实况记录明细中的排水特征、在线监测和采样条件状态 | success | 无 |
| L3_P2_SQL_004 | 按省市区县统计排污口总数和有实况记录的排污口数量，包含没有实况记录的排污口 | success | 无 |
| L3_P2_SQL_005 | 查询各断面每年度的全年水质目标等级记录 | success | 无 |
| L3_P2_SQL_006 | 按年度和目标水质等级统计考核断面数量 | success | 无 |
| L3_P2_SQL_007 | 查询断面编码、名称及所属水体编码、名称和类型 | success | 无 |
| L3_P2_SQL_008 | 统计各水体对应的断面数量，包含没有断面的水体 | success | 无 |
| L3_P2_SQL_011 | 查询排污口国家编码、名称、整治状态、规范化状态及实况在线监测状态联合记录明细 | success | 无 |

## 结论

9 条 approved P2 样本受控写入完成；2 条 excluded 样本未写入。
