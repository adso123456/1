// sourceDataPreservation.test.ts — B-10B: V2 ChartData 原始数据保存验证
//
// 验证 prepareChartV2 / prepareChartV2All 成功时 chart.sourceColumns/sourceRows
// 保存原始输入，而 chart.columns/rows 保持 transform 后输出。

import { prepareChartV2, prepareChartV2All } from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, msg?: string): void {
  if (actual !== expected) {
    throw new Error(msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertOk(cond: boolean, msg?: string): void {
  if (!cond) throw new Error(msg ?? `expected truthy, got ${JSON.stringify(cond)}`);
}

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (err) {
    failed++;
    console.error(`\nFAIL: ${name}`);
    console.error(`  ${(err as Error).message}`);
  }
}

// ============================================================
// T1: group_by_sum 图表保存 source 数据
// ============================================================

test('T1: group_by_sum 图表 sourceColumns/sourceRows 保存原始输入', () => {
  // 重复 key 的明细数据 → Planner 选 bar_categorical_aggregated (group_by_sum)
  const sourceColumns = ['region', 'count'];
  const sourceRows: Row[] = [
    { region: '城北', count: 10 },
    { region: '城北', count: 15 },
    { region: '城南', count: 8 },
    { region: '城南', count: 12 },
    { region: '城东', count: 20 },
  ];

  const result = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 't1',
    title: 'Group By Sum Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  assertOk(result.chart !== null, 'chart 不应为 null');

  const chart = result.chart!;

  // sourceColumns/sourceRows 应保存原始输入
  assertOk(Array.isArray(chart.sourceColumns), 'sourceColumns 应存在');
  assertEqual(chart.sourceColumns!.length, 2, 'sourceColumns 应含 2 列');
  assertOk(chart.sourceColumns!.includes('region'), 'sourceColumns 应含 region');
  assertOk(chart.sourceColumns!.includes('count'), 'sourceColumns 应含 count');

  assertOk(Array.isArray(chart.sourceRows), 'sourceRows 应存在');
  assertEqual(chart.sourceRows!.length, 5, 'sourceRows 应含 5 行（原始明细）');

  // columns/rows 应是 transform 后数据（group_by_sum 聚合后应变少）
  assertOk(chart.columns.length >= 2, 'transform 后 columns 应至少 2 列');
  assertOk(chart.rows.length < sourceRows.length,
    `transform 后 rows (${chart.rows.length}) 应少于原始 (${sourceRows.length})`);
});

// ============================================================
// T2: boxplot_summary 图表保存 source 数据
// ============================================================

test('T2: boxplot_summary 图表 sourceColumns/sourceRows 保存原始输入', () => {
  const sourceColumns = ['station', 'ph_value'];
  const sourceRows: Row[] = [
    { station: '站点A', ph_value: 7.2 },
    { station: '站点A', ph_value: 7.5 },
    { station: '站点A', ph_value: 6.8 },
    { station: '站点B', ph_value: 8.1 },
    { station: '站点B', ph_value: 7.9 },
    { station: '站点B', ph_value: 7.4 },
  ];

  const result = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
    id: 't2',
    title: 'Boxplot Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  assertOk(result.chart !== null, 'chart 不应为 null');

  const chart = result.chart!;

  // source 保存原始
  assertOk(Array.isArray(chart.sourceColumns), 'sourceColumns 应存在');
  assertEqual(chart.sourceColumns!.length, 2);
  assertOk(Array.isArray(chart.sourceRows), 'sourceRows 应存在');
  assertEqual(chart.sourceRows!.length, 6);

  // transform 后 columns 含五数概括字段
  assertOk(chart.columns.includes('min'), 'transform 后应含 min');
  assertOk(chart.columns.includes('q1'), 'transform 后应含 q1');
  assertOk(chart.columns.includes('median'), 'transform 后应含 median');
  assertOk(chart.columns.includes('q3'), 'transform 后应含 q3');
  assertOk(chart.columns.includes('max'), 'transform 后应含 max');

  // sourceColumns 不应被 transform 污染
  assertOk(!chart.sourceColumns!.includes('min'), 'sourceColumns 不应含 min');
  assertOk(chart.sourceColumns!.includes('station'), 'sourceColumns 应含 station');
  assertOk(chart.sourceColumns!.includes('ph_value'), 'sourceColumns 应含 ph_value');
});

// ============================================================
// T3: prepareChartV2（PILOT）也填充 sourceColumns/sourceRows
// ============================================================

test('T3: prepareChartV2 成功时也填充 sourceColumns/sourceRows', () => {
  const sourceColumns = ['product', 'sales'];
  const sourceRows: Row[] = [
    { product: 'A', sales: 100 },
    { product: 'B', sales: 200 },
    { product: 'C', sales: 150 },
  ];

  const result = prepareChartV2({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'auto',
    intent: 'auto',
    id: 't3',
    title: 'PILOT Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2 应成功: ${result.errorCode}`);
  assertOk(result.chart !== null);

  const chart = result.chart!;
  assertOk(Array.isArray(chart.sourceColumns), 'PILOT sourceColumns 应存在');
  assertEqual(chart.sourceColumns!.length, 2);
  assertOk(Array.isArray(chart.sourceRows), 'PILOT sourceRows 应存在');
  assertEqual(chart.sourceRows!.length, 3);
});

// ============================================================
// T4: 失败结果不需要 sourceRows（chart 为 null）
// ============================================================

test('T4: 失败结果 chart 为 null，不要求 sourceRows', () => {
  const result = prepareChartV2All({
    columns: ['id', 'station_name', 'sample_date', 'ph', 'cod'],
    rows: Array.from({ length: 10 }, (_, i) => ({
      id: i,
      station_name: '站' + (i % 3),
      sample_date: '2024-01-0' + ((i % 9) + 1),
      ph: 7 + i % 3,
      cod: 12 + i % 5,
    })),
    source: 'auto',
    intent: 'auto',
    id: 't4',
    title: 'Detail Rows',
    dataVersion: 1,
  });

  assertEqual(result.ok, false, 'detail_rows auto 应失败');
  assertEqual(result.chart, null, '失败时 chart 应为 null');
  // chart 为 null，不存在 sourceColumns/sourceRows 的概念 — 此断言仅确认不抛异常
});

// ============================================================
// T5: transform='none' 的图表也保存 source
// ============================================================

test('T5: transform=none 图表 sourceColumns/sourceRows 等于原始输入', () => {
  const sourceColumns = ['region', 'count'];
  const sourceRows: Row[] = [
    { region: '城北', count: 45 },
    { region: '城南', count: 32 },
    { region: '城东', count: 28 },
  ];

  const result = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'auto',
    intent: 'auto',
    id: 't5',
    title: 'None Transform Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  const chart = result.chart!;

  assertOk(Array.isArray(chart.sourceColumns));
  assertEqual(chart.sourceColumns!.length, sourceColumns.length);
  assertOk(Array.isArray(chart.sourceRows));
  assertEqual(chart.sourceRows!.length, sourceRows.length);

  // transform='none' 时 columns/rows 是浅拷贝，内容应与 source 一致
  assertEqual(chart.columns.length, sourceColumns.length);
  assertEqual(chart.rows.length, sourceRows.length);
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Source Data Preservation Tests (B-10B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
