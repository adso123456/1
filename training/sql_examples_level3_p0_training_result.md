# 第 3 级 P0 approved SQL 示例受控写入结果

## 汇总

- 当前工作目录：E:\3\posgresql\1
- git remote -v：
```text
origin	https://github.com/adso123456/1.git (fetch)
origin	https://github.com/adso123456/1.git (push)
```
- 当前 commit：898458e115d5017f4059d81aec9f8d8f405767bd
- 初始 git status --short：
```text
clean
```
- 修改/新增文件路径：training/train_sql_examples_level3_p0.py, training/sql_examples_level3_p0_training_result.md
- 是否启动真实主服务：否
- 是否连接数据库：否
- 是否执行真实 SQL：否
- 是否调用 DeepSeek：否
- 是否调用 vn.train()：否
- 是否使用 memory.save_tool_usage：是
- 是否写入正式 ChromaDB：是
- 是否修改正式 vanna_data：是
- 备份目录：E:\3\_backup\level3_p0_training_20260710_145745
- 是否只写 approved：是
- requires_manual_review 写入数量：0
- excluded 写入数量：0

## 写入前指纹摘要

- 文件数量：5
- 总大小：682148
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=dc253996916c68ec30b8035eac78cb34d04e13019b645a7ed9866a10c90d6678
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=7a12e561363385e9dfeeab326368731c030ed4b374e7f5897ac819159d2884c5
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- vanna_data/chroma.sqlite3 | size=462848 | sha256=00a1601cfd160785c0e7763833334267df0ecd70d4f4e47e4ace84807ccf9f52

## 写入后指纹摘要

- 文件数量：5
- 总大小：817316
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin | size=218800 | sha256=7c72d22937d5090e5b603d6fa5b7708eaf523a858db4dca5b1e3ec50842f01c8
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/header.bin | size=100 | sha256=5c8a407226e15554f8aa5e2dc70831bc8e464bd1433ac370e1dc9bef7e839d5a
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin | size=400 | sha256=d54c9c3c7bb416d54d77d1893dc2d9710a3b018fbc178b3f57b7442134b0aa27
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/link_lists.bin | size=0 | sha256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
- vanna_data/chroma.sqlite3 | size=598016 | sha256=1c1f88077b2ff70aa7b028cd13c446677e7ce4ffbce596bb4714add03651072e

## 变化文件列表

- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/data_level0.bin
- vanna_data/68092f4b-e2a5-4ccd-b126-95b1218cb050/length.bin
- vanna_data/chroma.sqlite3

## 写入统计

| 项目 | 数量 |
|---|---:|
| 样本总数 | 18 |
| 写入成功数量 | 18 |
| 写入失败数量 | 0 |
| skipped 数量 | 0 |
| requires_manual_review 写入数量 | 0 |
| excluded 写入数量 | 0 |

## 逐样本写入结果

| sample_id | question | tool_name | training_level | expected_tables | write_status | error |
|---|---|---|---|---|---|---|
| L3_P0_SQL_001 | 查看某站点年度水质趋势 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_002 | 某站点年度水质各指标汇总 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_003 | 对比两个站点的pH和溶解氧年度变化 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_004 | 查询年度pH年均值最高的站点列表 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_005 | 查询年度水质较差站点排名 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_006 | 查询某站点水质月趋势中的氨氮和总氮 | run_sql | level3_p0_sql_examples | wm_waterquality_month_records | success | 无 |
| L3_P0_SQL_007 | 查看某站点近两年pH和溶解氧月变化趋势 | run_sql | level3_p0_sql_examples | wm_waterquality_month_records | success | 无 |
| L3_P0_SQL_008 | 某站点不同水质等级在月记录中的分布 | run_sql | level3_p0_sql_examples | wm_waterquality_month_records | success | 无 |
| L3_P0_SQL_009 | 某站点年度水温变化趋势 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_010 | 某站点年度化学需氧量变化趋势 | run_sql | level3_p0_sql_examples | wm_waterquality_year_records | success | 无 |
| L3_P0_SQL_011 | 查看某站点日记录中pH值最高的记录 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |
| L3_P0_SQL_012 | 查看某站点日记录中pH值最低的记录 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |
| L3_P0_SQL_013 | 查询某站点小时记录中溶解氧低于5.0mg/L的时段 | run_sql | level3_p0_sql_examples | wm_waterquality_hour_records | success | 无 |
| L3_P0_SQL_014 | 对比多个水质指标在某站点的日平均值 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |
| L3_P0_SQL_015 | 查询某站点最近一个月各水质等级天数统计 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |
| L3_P0_SQL_016 | 某站点日记录中水质等级为劣V类的记录 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |
| L3_P0_SQL_017 | 查询月度水质为I至III类的站点列表 | run_sql | level3_p0_sql_examples | wm_waterquality_month_records | success | 无 |
| L3_P0_SQL_018 | 某站点日记录中氨氮值最高的记录 | run_sql | level3_p0_sql_examples | wm_waterquality_day_records | success | 无 |

## 写入后约束确认

- 正式 agent_data/query_results_*.csv 是否新增：否
- vanna_data 变化是否符合预期：是
- 是否调用 vn.train()：否
- 是否启动主服务：否
- 是否连接数据库：否
- 是否执行 SQL：否
- 是否调用 DeepSeek：否

## 当前结论

通过：18 条 approved 样本均已写入。

## 下一阶段建议

在隔离环境中做第 3 级 P0 写入后最小问答验证；不进入第 4 级。
