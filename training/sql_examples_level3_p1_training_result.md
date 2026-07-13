# Level 3 P1 approved SQL 示例受控写入结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- 当前 commit：3438c7968020ebba2e777fac3529e193b070b453
- 初始 git status --short：
```text
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
```
- 是否调用 vn.train()：否
- 是否使用 memory.save_tool_usage()：是
- 是否写入正式 ChromaDB：是
- 批准写入数量：21
- 写入成功数量：21
- 写入失败数量：0
- requires_manual_review 写入数量：0
- excluded 写入数量：0
- 备份目录：E:\3\_backup\level3_p1_training_20260713_135814
- 正式 query_results 新增数量：0

## 写入前后指纹

- 写入前文件数量：5
- 写入后文件数量：5

### 写入前

- 68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=cc5bfaf1dd4096dc90e44a5104412f218cf22c4f155d14d0a7b5fe6867539be5
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=dfe5853241ccf6b2e70b86cf7fc75670356aa92348ad15c0a598a5a88a1be7b9
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- chroma.sqlite3 | size=598016 | sha256=6c89ae7dff7cc15d9854812d71a75abac14a7af5c310211103ffa1488b1a2527

### 写入后

- 68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=701d5e915735e6448bb699d09cb0a6fa4ee60e2f9cc0fa4d32b7ad5e2b41f6a7
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=78c470c832f4af1ce7b4bc0ba44d40d401462b248d6101a7b682feb8fe6e3c81
- 68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- chroma.sqlite3 | size=761856 | sha256=179f687a03656e28a404a8405fb56868184123ced196f0f9503fcfb6b840241b

## 变化文件列表

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
- vanna_data/chroma.sqlite3

## 逐样本写入状态

| sample_id | question | status | error |
|---|---|---|---|
| L3_P1_SQL_001 | 查询排污口监测数据中的COD和氨氮记录 | success | 无 |
| L3_P1_SQL_002 | 查看排污口最近的pH、BOD和流量监测记录 | success | 无 |
| L3_P1_SQL_003 | 查询排污口排水特征和在线监测状态 | success | 无 |
| L3_P1_SQL_006 | 查询排污口规范化建设状态 | success | 无 |
| L3_P1_SQL_007 | 查询PS类型排水口的COD、总氮和pH日记录 | success | 无 |
| L3_P1_SQL_008 | 查询废水小时流量、排放量和状态趋势 | success | 无 |
| L3_P1_SQL_009 | 查询PS类型废水月度COD、总氮、pH和排放数据 | success | 无 |
| L3_P1_SQL_011 | 查询断面编码、名称、级别、属性和考核状态 | success | 无 |
| L3_P1_SQL_012 | 查询水文站基础信息和建设状态 | success | 无 |
| L3_P1_SQL_013 | 按城市统计水文站记录数 | success | 无 |
| L3_P1_SQL_014 | 查询水体基础信息、类型、功能和所在流域 | success | 无 |
| L3_P1_SQL_015 | 查询摄像头设备基础信息和监控对象 | success | 无 |
| L3_P1_SQL_016 | 查询摄像头平台设备、厂商、型号和在线状态 | success | 无 |
| L3_P1_SQL_017 | 查询无人机名称、品牌、型号和在线状态 | success | 无 |
| L3_P1_SQL_018 | 查询区县级行政区名称、编码和所属城市 | success | 无 |
| L3_P1_SQL_019 | 按水源类型统计普通取水口记录数 | success | 无 |
| L3_P1_SQL_020 | 按城市和区县查询普通取水口 | success | 无 |
| L3_P1_SQL_021 | 查询普通取水口行政区域和使用状态 | success | 无 |
| L3_P1_SQL_022 | 查询水源地名称、类型、状态和所在区域 | success | 无 |
| L3_P1_SQL_023 | 查询水源地保护等级和保护区划定状态 | success | 无 |
| L3_P1_SQL_024 | 按年实际取水量从高到低查看水源地及其服务人口 | success | 无 |

## 结论

21 条 approved 样本受控写入完成；3 条 requires_manual_review 样本未写入。
