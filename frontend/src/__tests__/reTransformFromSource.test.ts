// reTransformFromSource.test.ts — B-10B: 基于 source 数据重新 transform 验证
//
// 验证从 V2 图表（含 sourceColumns/sourceRows）重新规划时：
// 1. 基于原始数据，不是基于已 transform 的数据
// 2. 不会发生二次聚合或字段丢失
// 3. boxplot / heatmap 等需要特殊 transform 的类型能正确生成

import { prepareChartV2All } from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';
import type { ChartData } from '../types.js';

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

/**
 * 模拟 useSSE 中从旧图表的 source 数据重新调用 V2 planner。
 */
function rePlanFromSource(
  oldChart: ChartData,
  requestedType: string,
) {
  const sourceColumns = oldChart.sourceColumns ?? oldChart.columns;
  const sourceRows = oldChart.sourceRows ?? oldChart.rows;

  return prepareChartV2All({
    columns: sourceColumns as string[],
    rows: sourceRows as Row[],
    source: 'user',
    intent: 'auto',
    requestedChartType: requestedType as any,
    id: oldChart.id,
    title: oldChart.title,
    dataVersion: oldChart.dataVersion,
  });
}

// ============================================================
// T1: detail_rows → bar (group_by_sum) → boxplot (from source)
// ============================================================

test('T1: group_by_sum bar → boxplot from source，不二次聚合', () => {
  // 明细数据（含 group 维度 + 数值）：足够行数让 boxplot 有意义
  const sourceColumns = ['station', 'ph_value'];
  const sourceRows: Row[] = [
    { station: '站点A', ph_value: 7.2 },
    { station: '站点A', ph_value: 7.5 },
    { station: '站点A', ph_value: 6.8 },
    { station: '站点A', ph_value: 7.0 },
    { station: '站点B', ph_value: 8.1 },
    { station: '站点B', ph_value: 7.9 },
    { station: '站点B', ph_value: 7.4 },
    { station: '站点B', ph_value: 8.3 },
    { station: '站点C', ph_value: 6.5 },
    { station: '站点C', ph_value: 6.9 },
    { station: '站点C', ph_value: 6.2 },
    { station: '站点C', ph_value: 7.1 },
  ];

  // 第一步：生成 bar（应该选 bar_categorical_comparison，transform=none
  // 因为重复 key 但 source: 'user' + requestedChartType）
  const barResult = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'rt1',
    title: 'Bar First',
    dataVersion: 1,
  });

  assertOk(barResult.ok, `第一步 bar 应成功: ${barResult.errorCode}`);
  const barChart = barResult.chart!;

  // 确认 bar chart 有 source 数据
  assertOk(Array.isArray(barChart.sourceRows), 'bar chart 应有 sourceRows');
  assertEqual(barChart.sourceRows!.length, sourceRows.length,
    'bar sourceRows 长度应等于原始数据行数');

  // 第二步：从 bar chart 的 source 数据切换到 boxplot
  const boxplotResult = rePlanFromSource(barChart, 'boxplot');

  assertOk(boxplotResult.ok, `第二步 boxplot 应成功: ${boxplotResult.errorCode}`);
  const boxplotChart = boxplotResult.chart!;

  assertEqual(boxplotChart.spec.type, 'boxplot');

  // 确认 boxplot 是基于原始 12 行数据生成的，不是基于聚合后的 rows
  assertOk(boxplotChart.columns.includes('min'), 'boxplot columns 含 min');
  assertOk(boxplotChart.columns.includes('q1'), 'boxplot columns 含 q1');
  assertOk(boxplotChart.columns.includes('median'), 'boxplot columns 含 median');
  assertOk(boxplotChart.columns.includes('q3'), 'boxplot columns 含 q3');
  assertOk(boxplotChart.columns.includes('max'), 'boxplot columns 含 max');

  // source 数据应与原始一致
  assertEqual(boxplotChart.sourceRows!.length, sourceRows.length,
    'boxplot sourceRows 应仍保留原始 12 行');

  // transform 后的行数应是分组数（3 个 station）
  assertEqual(boxplotChart.rows.length, 3,
    'boxplot rows 应为 3（对应 3 个 station 分组）');

  // 不应从 bar 的 transform 输出（2 列聚合行）二次聚合
  // bar 输出是 [station, ph_value]（categorical_comparison 用 none transform）
  // 如果错误地基于 bar 的 rows（也是 12 行，因为 none transform 是浅拷贝）来生成 boxplot
  // 结果仍应正确，但 sourceRows 确认了使用的是原始数据
  assertOk(boxplotChart.sourceColumns!.includes('ph_value'),
    'sourceColumns 应含原始列名 ph_value');
});

// ============================================================
// T2: group_by_sum bar（含重复 key）→ line from source
// ============================================================

test('T2: 从 group_by_sum bar 切换到 line → 基于 source 不二次聚合', () => {
  // 时间序列数据支持 line → 从 line 切到 bar 再切回来，验证 source 不丢失
  const sourceColumns = ['month', 'discharge'];
  const sourceRows: Row[] = [
    { month: '1月', discharge: 120 },
    { month: '2月', discharge: 115 },
    { month: '3月', discharge: 130 },
    { month: '4月', discharge: 125 },
    { month: '5月', discharge: 140 },
  ];

  // 第一步：auto 生成 line
  const lineResult = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'auto',
    intent: 'auto',
    id: 'rt2',
    title: 'Line first',
    dataVersion: 1,
  });

  assertOk(lineResult.ok, `line 应成功: ${lineResult.errorCode}`);
  const lineChart = lineResult.chart!;
  assertEqual(lineChart.spec.type, 'line');
  assertEqual(lineChart.sourceRows!.length, sourceRows.length,
    'sourceRows 应与原始行数一致');

  // 第二步：从 source 数据切换到 bar
  const barResult = rePlanFromSource(lineChart, 'bar');

  assertOk(barResult.ok, `切换到 bar 应成功: ${barResult.errorCode}`);
  assertEqual(barResult.chart!.spec.type, 'bar');
  assertEqual(barResult.chart!.sourceRows!.length, sourceRows.length,
    'bar sourceRows 长度应与原始一致（不基于 transform 行二次聚合）');

  // 第三步：再切回 line
  const lineResult2 = rePlanFromSource(barResult.chart!, 'line');
  assertOk(lineResult2.ok, `再切回 line 应成功: ${lineResult2.errorCode}`);
  assertEqual(lineResult2.chart!.spec.type, 'line');
  assertEqual(lineResult2.chart!.sourceRows!.length, sourceRows.length,
    '再次切回 line 后 sourceRows 长度仍等于原始');

  console.log(`  [info] line → bar → line，sourceRows 始终保持 ${sourceRows.length} 行`);
});

// ============================================================
// T3: 多次切换链式验证
// ============================================================

test('T3: bar → line → bar 链式切换均从 source 重规划', () => {
  const sourceColumns = ['month', 'discharge'];
  const sourceRows: Row[] = [
    { month: '1月', discharge: 120 },
    { month: '2月', discharge: 115 },
    { month: '3月', discharge: 130 },
    { month: '4月', discharge: 125 },
    { month: '5月', discharge: 140 },
  ];

  // 第一步：auto 生成（应为 line）
  const step1 = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'auto',
    intent: 'auto',
    id: 'rt3',
    title: 'Chain',
    dataVersion: 1,
  });

  assertOk(step1.ok, `step1 auto 应成功: ${step1.errorCode}`);
  const chart1 = step1.chart!;
  assertEqual(chart1.sourceRows!.length, 5, 'step1 sourceRows 应为 5');

  // 第二步：切换到 bar（from source）
  const step2 = rePlanFromSource(chart1, 'bar');
  assertOk(step2.ok, `step2 bar 应成功: ${step2.errorCode}`);
  const chart2 = step2.chart!;
  assertEqual(chart2.spec.type, 'bar');
  assertEqual(chart2.sourceRows!.length, 5, 'step2 sourceRows 仍应为 5');

  // 第三步：再切回 line（from source）
  const step3 = rePlanFromSource(chart2, 'line');
  assertOk(step3.ok, `step3 line 应成功: ${step3.errorCode}`);
  const chart3 = step3.chart!;
  assertEqual(chart3.spec.type, 'line');
  assertEqual(chart3.sourceRows!.length, 5, 'step3 sourceRows 仍应为 5');

  // 三次切换后 sourceRows 长度始终等于原始
  console.log(`  [info] 三次切换: ${chart1.spec.type} → ${chart2.spec.type} → ${chart3.spec.type}`);
});

// ============================================================
// T4: 对比：基于 transform 后数据 vs 基于 source 数据
// ============================================================

test('T4: transform 后数据行数与 source 行数对比验证', () => {
  // 用 boxplot 数据验证：sourceRows 保留原始，transform 后为分组数
  const sourceColumns = ['station', 'ph_value'];
  const sourceRows: Row[] = [
    { station: '站点A', ph_value: 7.2 },
    { station: '站点A', ph_value: 7.5 },
    { station: '站点A', ph_value: 6.8 },
    { station: '站点A', ph_value: 7.0 },
    { station: '站点B', ph_value: 8.1 },
    { station: '站点B', ph_value: 7.9 },
    { station: '站点B', ph_value: 7.4 },
    { station: '站点B', ph_value: 8.3 },
  ];

  const result = prepareChartV2All({
    columns: sourceColumns,
    rows: sourceRows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
    id: 'rt4',
    title: 'Row Count',
    dataVersion: 1,
  });

  assertOk(result.ok, `boxplot 应成功: ${result.errorCode}`);
  const chart = result.chart!;

  assertEqual(chart.sourceRows!.length, 8, 'sourceRows 应为 8（原始行数）');
  // boxplot_summary transform 后每个 station 一行
  assertOk(chart.rows.length < 8,
    `transform 后 rows(${chart.rows.length}) 应 < 8（boxplot 分组聚合）`);
  assertEqual(chart.rows.length, 2, 'boxplot 后应为 2 行（2 个 station）');

  console.log(`  [info] 原始 8 行 → boxplot transform 后 ${chart.rows.length} 行，sourceRows 保持 8 行`);
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Re-Transform From Source Tests (B-10B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
