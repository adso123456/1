# 第 2 级 SQL 示例训练写入结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 初始 git status --short：
```text
clean
```
- 当前基础 commit：00e5becc3692530236202ffe55c87265ed137934
- 备份目录：E:\3\_backup\level2_sql_training_20260709_154225
- approved 样本总数：16
- 实际训练样本总数：16
- 实际训练样本 ID 列表：L2_SQL_001, L2_SQL_002, L2_SQL_003, L2_SQL_004, L2_SQL_005, L2_SQL_006, L2_SQL_007, L2_SQL_008, L2_SQL_009, L2_SQL_010, L2_SQL_013, L2_SQL_014, L2_SQL_015, L2_SQL_016, L2_SQL_017, L2_SQL_018
- 明确未训练样本 ID：L2_SQL_011, L2_SQL_012, L2_SQL_019
- requires_manual_review 样本：L2_SQL_011, L2_SQL_012, L2_SQL_019
- excluded 样本：无
- 是否训练 requires_manual_review：否
- 是否训练 excluded：否
- 是否执行真实 SQL：否
- 是否连接数据库：否
- 是否启动真实主服务：否
- 是否写入 ChromaDB：是
- 是否修改数据库结构：否
- 是否进入第 3/4 级：否
- 训练是否全部成功：是

## 训练前 ChromaDB 指纹

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=05cc80bd13fe8426e6631f0f0eebb05067473b35ab33c1c65123733c2bf090c4
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=a49fb6f75c9fd0242e51ce29341fa3d64c2aed2e382f52f5de379836865aa569
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- vanna_data/chroma.sqlite3 | size=385024 | sha256=1b700c7faedde6a04c59611f1c1f686df05ed71f7c27b2659e47eab21ec9caee

## 训练后 ChromaDB 指纹

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=dc253996916c68ec30b8035eac78cb34d04e13019b645a7ed9866a10c90d6678
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- vanna_data/chroma.sqlite3 | size=462848 | sha256=00a1601cfd160785c0e7763833334267df0ecd70d4f4e47e4ace84807ccf9f52

## ChromaDB 变化文件列表

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
- vanna_data/chroma.sqlite3

## 训练明细

### L2_SQL_001

- id：L2_SQL_001
- question：某站点最近一段时间水质日变化趋势
- used_tables：wm_waterquality_day_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_002

- id：L2_SQL_002
- question：按站点查看水质日记录数量和最近监测时间
- used_tables：wm_waterquality_day_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_003

- id：L2_SQL_003
- question：查询某站点水质日趋势中的 pH 和溶解氧变化
- used_tables：wm_waterquality_day_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_004

- id：L2_SQL_004
- question：某站点水质小时变化趋势
- used_tables：wm_waterquality_hour_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_005

- id：L2_SQL_005
- question：按小时查看某站点水质等级分布
- used_tables：wm_waterquality_hour_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_006

- id：L2_SQL_006
- question：查询最近小时水质监测中的 pH 和氨氮指标
- used_tables：wm_waterquality_hour_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_007

- id：L2_SQL_007
- question：某站点水质月变化趋势
- used_tables：wm_waterquality_month_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_008

- id：L2_SQL_008
- question：按月份统计水质月记录数量
- used_tables：wm_waterquality_month_records
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_009

- id：L2_SQL_009
- question：查询排污口编码
- used_tables：rs_outlet
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_010

- id：L2_SQL_010
- question：查看排污口国家编码和地方编码
- used_tables：rs_outlet_info_v2
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_013

- id：L2_SQL_013
- question：查询排污口基础信息
- used_tables：rs_outlet
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_014

- id：L2_SQL_014
- question：按区县统计排污口数量
- used_tables：rs_outlet
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_015

- id：L2_SQL_015
- question：查询站点名称和所属区域
- used_tables：wm_station_info_v2
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_016

- id：L2_SQL_016
- question：按区域统计监测站点数量
- used_tables：wm_station_info_v2
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_017

- id：L2_SQL_017
- question：查询区域编码和区域名称
- used_tables：gis_region
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

### L2_SQL_018

- id：L2_SQL_018
- question：查询取水口名称和水源类型
- used_tables：wm_water_intake
- SQL Guard result：passed=True；severity=ok；reason=SQL 静态校验通过
- 写入结果：成功
- 错误信息：无

## 训练后 Git 状态

```text
M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
 M vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
 M vanna_data/chroma.sqlite3
?? training/train_sql_examples_level2.py
?? training/sql_examples_level2_training_result.md
```

## 结论

- 当前结论：第 2 级 approved SQL 示例已受控写入 ChromaDB；requires_manual_review 与 excluded 样本未训练。
- 下一步建议：先做最小问答验证，确认 P0 上下文、SQL Guard 和新写入 SQL 示例的协同效果；不要进入第 3/4 级，除非另起阶段审批。
- 回滚方式：如需回滚，将备份目录中的 vanna_data 覆盖回 E:\3\posgresql\1\vanna_data
