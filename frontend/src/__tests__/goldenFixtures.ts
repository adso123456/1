// goldenFixtures.ts — V2 Dataset Profiler 的 15 个 Golden 测试夹具
//
// 每个夹具提供 columns + rows，预期结果在测试文件中断言。

import type { Row } from '../datasetProfilerV2.js';

export interface GoldenFixture {
  name: string;
  columns: string[];
  rows: Row[];
}

export const FIXTURES: GoldenFixture[] = [
  // ── 1. 空数据 ──
  {
    name: 'empty',
    columns: [],
    rows: [],
  },

  // ── 2. 单 KPI ──
  {
    name: 'single_kpi',
    columns: ['total_count'],
    rows: [
      { total_count: 342 },
    ],
  },

  // ── 3. 单行多指标 ──
  {
    name: 'single_row_multi_measure',
    columns: ['station_count', 'avg_discharge', 'total_violations'],
    rows: [
      { station_count: 120, avg_discharge: 350.5, total_violations: 15 },
    ],
  },

  // ── 4. 带唯一 ID 的监测明细 ──
  //    避免：实体关键词（如 station/site）+ 时间字段 → multi_entity_temporal
  //    避免：identifier 关键词（如 code）→ dimensionFields 被清空
  //    使用 sampling_point（非实体、非标识、非时间）+ sample_id（仅 _id 匹配）
  {
    name: 'monitoring_detail_with_id',
    columns: ['sample_id', 'sampling_point', 'ph_value', 'cod_value', 'nh3n_value'],
    rows: [
      { sample_id: 1, sampling_point: 'SP-A', ph_value: 7.2, cod_value: 12.0, nh3n_value: 0.5 },
      { sample_id: 2, sampling_point: 'SP-B', ph_value: 7.8, cod_value: 18.0, nh3n_value: 0.8 },
      { sample_id: 3, sampling_point: 'SP-A', ph_value: 7.5, cod_value: 10.0, nh3n_value: 0.4 },
      { sample_id: 4, sampling_point: 'SP-C', ph_value: 7.0, cod_value: 8.0, nh3n_value: 0.3 },
      { sample_id: 5, sampling_point: 'SP-B', ph_value: 8.1, cod_value: 20.0, nh3n_value: 0.9 },
      { sample_id: 6, sampling_point: 'SP-A', ph_value: 6.8, cod_value: 15.0, nh3n_value: 0.6 },
    ],
  },

  // ── 5. region + count ──
  {
    name: 'region_count',
    columns: ['region', 'count'],
    rows: [
      { region: '城北区', count: 45 },
      { region: '城南区', count: 32 },
      { region: '城东区', count: 28 },
      { region: '城西区', count: 19 },
      { region: '中心区', count: 56 },
    ],
  },

  // ── 6. month + discharge ──
  {
    name: 'month_discharge',
    columns: ['month', 'discharge'],
    rows: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
      { month: '4月', discharge: 125 },
      { month: '5月', discharge: 140 },
      { month: '6月', discharge: 135 },
      { month: '7月', discharge: 150 },
      { month: '8月', discharge: 145 },
      { month: '9月', discharge: 138 },
      { month: '10月', discharge: 142 },
      { month: '11月', discharge: 148 },
      { month: '12月', discharge: 155 },
    ],
  },

  // ── 7. 完整多实体时间数据 ──
  {
    name: 'complete_multi_entity_temporal',
    columns: ['station_name', 'month', 'value'],
    rows: [
      { station_name: '站点A', month: '1月', value: 10 },
      { station_name: '站点A', month: '2月', value: 12 },
      { station_name: '站点A', month: '3月', value: 11 },
      { station_name: '站点B', month: '1月', value: 8 },
      { station_name: '站点B', month: '2月', value: 9 },
      { station_name: '站点B', month: '3月', value: 10 },
    ],
  },

  // ── 8. 不完整多实体时间数据 ──
  {
    name: 'incomplete_multi_entity_temporal',
    columns: ['station_name', 'month', 'value'],
    rows: [
      { station_name: '站点A', month: '1月', value: 10 },
      { station_name: '站点A', month: '2月', value: 12 },
      { station_name: '站点A', month: '3月', value: 11 },
      { station_name: '站点B', month: '1月', value: 8 },
      // 站点B 缺少 2月、3月
    ],
  },

  // ── 9. 两数值关系 ──
  {
    name: 'two_numeric',
    columns: ['rainfall', 'runoff'],
    rows: [
      { rainfall: 12.5, runoff: 3.2 },
      { rainfall: 25.0, runoff: 7.8 },
      { rainfall: 8.0, runoff: 1.9 },
      { rainfall: 30.5, runoff: 10.1 },
      { rainfall: 15.0, runoff: 4.5 },
      { rainfall: 22.0, runoff: 6.7 },
    ],
  },

  // ── 10. 三数值关系 ──
  {
    name: 'three_numeric',
    columns: ['rainfall', 'runoff', 'area'],
    rows: [
      { rainfall: 12.5, runoff: 3.2, area: 150 },
      { rainfall: 25.0, runoff: 7.8, area: 300 },
      { rainfall: 8.0, runoff: 1.9, area: 100 },
      { rainfall: 30.5, runoff: 10.1, area: 350 },
      { rainfall: 15.0, runoff: 4.5, area: 180 },
      { rainfall: 22.0, runoff: 6.7, area: 250 },
    ],
  },

  // ── 11. region + month + value (二维矩阵) ──
  {
    name: 'region_month_matrix',
    columns: ['region', 'month', 'avg_temp'],
    rows: [
      { region: '城北', month: '1月', avg_temp: 5.2 },
      { region: '城北', month: '2月', avg_temp: 7.1 },
      { region: '城北', month: '3月', avg_temp: 12.0 },
      { region: '城南', month: '1月', avg_temp: 6.0 },
      { region: '城南', month: '2月', avg_temp: 8.3 },
      { region: '城南', month: '3月', avg_temp: 13.5 },
      { region: '城东', month: '1月', avg_temp: 5.8 },
      { region: '城东', month: '2月', avg_temp: 7.9 },
      { region: '城东', month: '3月', avg_temp: 12.8 },
    ],
  },

  // ── 12. station + 多条 pH 样本 ──
  {
    name: 'station_ph_samples',
    columns: ['station', 'ph_value'],
    rows: [
      { station: '站点A', ph_value: 7.2 },
      { station: '站点A', ph_value: 7.5 },
      { station: '站点A', ph_value: 6.8 },
      { station: '站点B', ph_value: 8.1 },
      { station: '站点B', ph_value: 7.9 },
      { station: '站点B', ph_value: 7.4 },
    ],
  },

  // ── 13. 重复 region + count ──
  {
    name: 'repeated_region_count',
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 10 },
      { region: '城北', count: 15 },
      { region: '城南', count: 8 },
      { region: '城南', count: 12 },
    ],
  },

  // ── 14. station + 多指标 ──
  {
    name: 'station_multi_measure',
    columns: ['station_name', 'ph', 'do', 'cod', 'nh3n'],
    rows: [
      { station_name: '站点A', ph: 7.2, do: 6.5, cod: 12.0, nh3n: 0.5 },
      { station_name: '站点B', ph: 7.8, do: 5.8, cod: 18.0, nh3n: 0.8 },
      { station_name: '站点C', ph: 7.0, do: 7.2, cod: 8.0, nh3n: 0.3 },
    ],
  },

  // ── 15. metric_name + value 异构汇总 ──
  {
    name: 'heterogeneous_metric_rows',
    columns: ['metric_name', 'value'],
    rows: [
      { metric_name: 'BOD监测记录总数', value: 16 },
      { metric_name: '涉及排污口数量', value: 16 },
      { metric_name: '平均每个排污口记录数', value: 1 },
    ],
  },
];
