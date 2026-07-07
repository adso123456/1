// userSwitchV2.test.ts — B-10B: 用户打字切换图表 V2 路径测试
//
// 验证 prepareChartV2All({ source: 'user', requestedChartType })
// 从原始数据重新规划，而非基于 transform 后数据二次聚合。

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

// ============================================================
// 模拟 useSSE 中的 prepareUserSwitchChartV2 纯函数
// ============================================================

/**
 * 模拟 useSSE.ts 中打字切换的 V2 路径：
 * 从 oldChart.sourceRows/sourceColumns（或 lastDataRef）获取原始数据，
 * 调用 prepareChartV2All 重新规划。
 */
function prepareUserSwitchChartV2(
  oldChart: ChartData,
  lastDataRef: { columns: string[]; data: Row[] },
  switchType: string,
) {
  const sourceColumns = oldChart.sourceColumns ?? lastDataRef.columns;
  const sourceRows = oldChart.sourceRows ?? lastDataRef.data;

  return prepareChartV2All({
    columns: sourceColumns as string[],
    rows: sourceRows as Row[],
    source: 'user',
    intent: 'auto',
    requestedChartType: switchType as any,
    id: oldChart.id,
    title: oldChart.title,
    dataVersion: oldChart.dataVersion,
  });
}

// ============================================================
// T1: 从 group_by_sum 图表切换到 line — 基于 source 数据重规划
// ============================================================

test('T1: V2 图表切换到 bar → 基于 source 数据重规划', () => {
  // 用时间序列数据：auto 生成 line，然后切换到 bar
  const sourceData = {
    columns: ['month', 'discharge'],
    data: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
      { month: '4月', discharge: 125 },
      { month: '5月', discharge: 140 },
    ] as Row[],
  };

  // auto 生成图表（应为 line）
  const autoResult = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'auto',
    intent: 'auto',
    id: 'switch-test',
    title: 'Switch Test',
    dataVersion: 1,
  });

  assertOk(autoResult.ok, `V2 auto 应成功: ${autoResult.errorCode}`);
  const oldChart = autoResult.chart!;
  assertOk(oldChart.sourceRows !== undefined, 'auto chart 应有 sourceRows');

  // 模拟用户打字"换成柱状图"：
  const switchResult = prepareUserSwitchChartV2(oldChart, sourceData, 'bar');

  assertOk(switchResult.ok, `切换到 bar 应成功: ${switchResult.errorCode}`);
  assertEqual(switchResult.chart!.spec.type, 'bar',
    '切换后 type 应为 bar');

  // 新 chart 也有 sourceColumns/sourceRows
  assertOk(Array.isArray(switchResult.chart!.sourceColumns));
  assertOk(Array.isArray(switchResult.chart!.sourceRows));
  assertEqual(switchResult.chart!.sourceRows!.length, sourceData.data.length,
    '新 chart sourceRows 长度应等于原始数据行数');
});

// ============================================================
// T2: 从 group_by_sum 切换到 boxplot → 基于 source 数据重规划
// ============================================================

test('T2: group_by_sum 图表切换到 boxplot → 不基于聚合行二次 transform', () => {
  const sourceData = {
    columns: ['station', 'ph_value'],
    data: [
      { station: '站点A', ph_value: 7.2 },
      { station: '站点A', ph_value: 7.5 },
      { station: '站点A', ph_value: 6.8 },
      { station: '站点B', ph_value: 8.1 },
      { station: '站点B', ph_value: 7.9 },
      { station: '站点B', ph_value: 7.4 },
    ] as Row[],
  };

  // 先生成 bar（用 source: 'user' 强制选择）
  const barResult = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 't2',
    title: 'Bar to Boxplot',
    dataVersion: 1,
  });

  assertOk(barResult.ok, `bar 应成功: ${barResult.errorCode}`);
  const oldChart = barResult.chart!;

  // 用 source 数据切换到 boxplot
  const boxplotResult = prepareUserSwitchChartV2(oldChart, sourceData, 'boxplot');

  assertOk(boxplotResult.ok, `切换到 boxplot 应成功: ${boxplotResult.errorCode}`);
  assertEqual(boxplotResult.chart!.spec.type, 'boxplot');

  // 确认 boxplot 是基于原始数据生成的（columns 含五数概括字段）
  assertOk(boxplotResult.chart!.columns.includes('min'), 'boxplot columns 应含 min');
  assertEqual(boxplotResult.chart!.sourceRows!.length, sourceData.data.length,
    'sourceRows 仍保留原始行数');
});

// ============================================================
// T3: 用户请求不可用类型 → V2 fallback 或失败
// ============================================================

test('T3: 用户请求 gauge 但数据不适合 → V2 有 fallbackNotice 或失败', () => {
  const sourceData = {
    columns: ['region', 'count'],
    data: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
      { region: '城东', count: 28 },
    ] as Row[],
  };

  const result = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'gauge',
    id: 't3',
    title: 'Gauge Request',
    dataVersion: 1,
  });

  // gauge 需要 single_value → 此数据是 categorical_series，不匹配
  // 但 Planner 的 selectForUser 有 fallbackNotice 机制：
  // requestedType 不可用 → 回退到第一个 supported plan
  if (result.ok) {
    // 回退成功 — chart 类型应与请求不同
    console.log(`  [info] gauge requested but got ${result.chart?.spec.type}, fallbackNotice: ${result.planning.fallbackNotice ?? 'null'}`);
    assertOk(result.planning.fallbackNotice !== null,
      '应有 fallbackNotice 说明请求类型不可用');
  } else {
    // 完全无法生成任何图表 — OK
    console.log(`  [info] gauge 请求完全失败: ${result.errorCode}`);
    assertOk(result.errorCode !== null);
  }
});

// ============================================================
// T4: 切换后 chart 带有 explicitType=true 和 v2Meta
// ============================================================

test('T4: 切换后 chart 带 explicitType=true 和 v2Meta', () => {
  const sourceData = {
    columns: ['month', 'discharge'],
    data: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
    ] as Row[],
  };

  // 先用 auto 生成 line（temporal_series → line）
  const autoResult = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'auto',
    intent: 'auto',
    id: 't4',
    title: 'Auto Line',
    dataVersion: 1,
  });

  assertOk(autoResult.ok, `auto 应成功: ${autoResult.errorCode}`);

  // 切换到 bar
  const switchResult = prepareUserSwitchChartV2(
    autoResult.chart!,
    sourceData,
    'bar',
  );

  assertOk(switchResult.ok, `切换到 bar 应成功: ${switchResult.errorCode}`);
  const switched = switchResult.chart!;

  assertEqual(switched.explicitType, true, '切换后 explicitType 应为 true');
  assertOk(switched.v2Meta !== undefined, '切换后应有 v2Meta');
  assertEqual(switched.v2Meta!.semanticMode, 'comparison',
    'bar 的 semanticMode 应为 comparison');
  assertOk(Array.isArray(switched.sourceColumns), 'sourceColumns 应存在');
  assertOk(Array.isArray(switched.sourceRows), 'sourceRows 应存在');
});

// ============================================================
// T5: 从非 V2 图表切换（无 sourceColumns）→ fallback 到 lastDataRef
// ============================================================

test('T5: 旧图表无 sourceColumns → fallback 到 lastDataRef', () => {
  // 模拟旧路径生成的图表（无 sourceColumns/sourceRows）
  const oldChart: ChartData = {
    id: 'old-chart',
    title: 'Old Chart',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
    ],
    spec: { type: 'bar', xField: 'region', yFields: ['count'] },
    // 没有 sourceColumns/sourceRows
  };

  const lastData = {
    columns: ['region', 'count'],
    data: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
      { region: '城东', count: 28 },
    ],
  };

  const result = prepareUserSwitchChartV2(oldChart, lastData, 'line');

  assertOk(result.ok, `应成功从 lastDataRef 获取数据: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'line');

  // source 来自 lastDataRef
  assertEqual(result.chart!.sourceRows!.length, 3,
    'sourceRows 长度应等于 lastDataRef 的行数');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`User Switch V2 Tests (B-10B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
