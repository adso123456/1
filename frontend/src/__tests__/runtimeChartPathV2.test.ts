// runtimeChartPathV2.test.ts — B-5C 运行时路径回归验证
//
// 模拟 useSSE.ts:777-888 的分支逻辑，确认：
// 1. auto no-chart-type → prepareChartV2All (ALL_CAPABILITIES_V2)
// 2. chart_spec 路径保留旧行为
// 3. chart_type 路径保留旧行为
//
// 不修改任何生产代码。不接入 React hooks。

import { prepareChartV2, prepareChartV2All } from '../chartPipelineV2.js';
import type { PrepareChartInputV2 } from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';

// ============================================================
// 断言
// ============================================================

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
// 模拟 useSSE.ts 分支逻辑（纯函数版）
// ============================================================

/**
 * 模拟 useSSE.ts:777-888 的分支决策。
 *
 * 参数对应 useSSE 中的变量：
 *   hasChartSpec   — extractAllChartSpecs 返回非空
 *   chartType      — extractChartType(content) 的返回值
 *   isWorthy       — isChartWorthy(columns, rows) 的返回值
 *
 * 返回对应路径标识：
 *   'chart_spec'     — AI 显式指定 chart_spec JSON
 *   'chart_type'     — AI 返回旧 chart_type 标记
 *   'v2_auto'        — V2 auto no-chart-type（当前唯一走 prepareChartV2All 的路径）
 *   'chart_none'     — ct === 'none'
 *   'no_chart'       — 有旧 ct 但数据不适合出图
 */
function classifyRuntimePath(
  hasChartSpec: boolean,
  chartType: string | null,
  isWorthy: boolean,
): 'chart_spec' | 'chart_type' | 'v2_auto' | 'chart_none' | 'no_chart' {
  if (hasChartSpec) return 'chart_spec';
  if (chartType && chartType !== 'none' && isWorthy) return 'chart_type';
  if (chartType === 'none') return 'chart_none';
  if (!chartType) return 'v2_auto';
  return 'no_chart';
}

// 共享测试数据
const BASIC_DATA = {
  columns: ['product', 'sales'] as string[],
  rows: [
    { product: 'A', sales: 100 },
    { product: 'B', sales: 200 },
  ] as Row[],
};

// ============================================================
// 1. 分支逻辑测试
// ============================================================

test('path: chart_spec → chart_spec 路径', () => {
  const path = classifyRuntimePath(true, null, true);
  assertEqual(path, 'chart_spec', '有 chart_spec 时走 chart_spec 路径');
});

test('path: chart_spec 覆盖旧 chart_type', () => {
  const path = classifyRuntimePath(true, 'bar', true);
  assertEqual(path, 'chart_spec', '有 chart_spec 时不受 chart_type 影响');
});

test('path: 旧 chart_type=bar → chart_type 路径', () => {
  const path = classifyRuntimePath(false, 'bar', true);
  assertEqual(path, 'chart_type', '旧 chart_type=bar + isWorthy → chart_type 路径');
});

test('path: 旧 chart_type=line → chart_type 路径', () => {
  const path = classifyRuntimePath(false, 'line', true);
  assertEqual(path, 'chart_type');
});

test('path: 旧 chart_type=pie → chart_type 路径', () => {
  const path = classifyRuntimePath(false, 'pie', true);
  assertEqual(path, 'chart_type');
});

test('path: chart_type=none → chart_none 路径', () => {
  const path = classifyRuntimePath(false, 'none', false);
  assertEqual(path, 'chart_none', 'ct=none 时清空 charts');
});

test('path: 无 chart_spec + 无 chart_type + 数据合适 → v2_auto 路径', () => {
  const path = classifyRuntimePath(false, null, true);
  assertEqual(path, 'v2_auto', '!ct 时走 V2 auto 路径');
});

test('path: 无 chart_spec + 无 chart_type + 数据不合适 → 仍走 v2_auto', () => {
  // useSSE 中 !ct 时不检查 isChartWorthy，直接交给 V2 Planner 判断
  const path = classifyRuntimePath(false, null, false);
  assertEqual(path, 'v2_auto', '!ct 时不检查 isWorthy，交给 V2 Planner');
});

test('path: chart_type=bar 但数据不合适 → no_chart', () => {
  const path = classifyRuntimePath(false, 'bar', false);
  assertEqual(path, 'no_chart', '有 ct 但数据不合适时清空 charts');
});

// ============================================================
// 2. auto no-chart-type 路径实际输出验证
// ============================================================

function makeInput(columns: string[], rows: Row[]): PrepareChartInputV2 {
  return {
    columns,
    rows,
    source: 'auto',
    intent: 'auto',
    id: 'runtime-test',
    title: 'Runtime Test',
    dataVersion: 1,
  };
}

test('runtime: single_kpi → gauge (ALL_CAPABILITIES_V2)', () => {
  const r = prepareChartV2All(makeInput(['total_count'], [{ total_count: 342 }]));
  assertOk(r.ok, `should succeed, got: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'gauge');
  assertEqual(r.chart!.explicitType, true);
});

test('runtime: region_count → bar', () => {
  const r = prepareChartV2All(makeInput(
    ['region', 'count'],
    [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
      { region: '城东', count: 28 },
    ],
  ));
  assertOk(r.ok, `should succeed, got: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'bar');
  assertEqual(r.selectedPlan!.variantId, 'bar_categorical_comparison');
});

test('runtime: month_discharge → line', () => {
  const r = prepareChartV2All(makeInput(
    ['month', 'discharge'],
    [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
      { month: '4月', discharge: 125 },
    ],
  ));
  assertOk(r.ok, `should succeed, got: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'line');
  assertEqual(r.selectedPlan!.variantId, 'line_temporal_trend_single');
});

test('runtime: two_numeric → scatter', () => {
  const r = prepareChartV2All(makeInput(
    ['rainfall', 'runoff'],
    [
      { rainfall: 12.5, runoff: 3.2 },
      { rainfall: 25.0, runoff: 7.8 },
      { rainfall: 8.0, runoff: 1.9 },
    ],
  ));
  assertOk(r.ok, `should succeed, got: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'scatter');
  assertEqual(r.selectedPlan!.variantId, 'scatter_numeric_relationship');
});

test('runtime: three_numeric → bubble', () => {
  const r = prepareChartV2All(makeInput(
    ['rainfall', 'runoff', 'area'],
    [
      { rainfall: 12.5, runoff: 3.2, area: 150 },
      { rainfall: 25.0, runoff: 7.8, area: 300 },
      { rainfall: 8.0, runoff: 1.9, area: 100 },
    ],
  ));
  assertOk(r.ok, `should succeed, got: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'bubble');
  assertEqual(r.selectedPlan!.variantId, 'bubble_numeric_relationship');
});

test('runtime: detail_rows → no_default_plan', () => {
  const r = prepareChartV2All(makeInput(
    ['id', 'station_name', 'sample_date', 'ph', 'cod'],
    Array.from({ length: 10 }, (_, i) => ({
      id: i,
      station_name: '站' + (i % 3),
      sample_date: '2024-01-0' + ((i % 9) + 1),
      ph: 7 + i % 3,
      cod: 12 + i % 5,
    })),
  ));
  assertEqual(r.ok, false);
  assertEqual(r.errorCode, 'no_default_plan');
  assertEqual(r.planning.profile.archetype, 'detail_rows');
});

// ============================================================
// 3. prepareChartV2 (PILOT) 仍可用 — 不受 B-5B 影响
// ============================================================

test('PILOT: prepareChartV2 仍正常运作', () => {
  const r = prepareChartV2(makeInput(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
      { product: 'C', sales: 150 },
    ],
  ));
  assertOk(r.ok);
  assertEqual(r.chart!.spec.type, 'bar');
});

test('PILOT: prepareChartV2 对 single_kpi 无 gauge（仅 3 种类型）', () => {
  const r = prepareChartV2(makeInput(
    ['total_count'],
    [{ total_count: 342 }],
  ));
  // PILOT 无 gauge → single_value 找不到匹配的 capability → no_default_plan
  assertEqual(r.ok, false);
  assertEqual(r.errorCode, 'no_default_plan');
});

// ============================================================
// 4. B-10B: user source + requestedChartType 路径
// ============================================================

test('user: source=user + requestedChartType=bar → 正确选择 bar', () => {
  const input = makeInput(BASIC_DATA.columns, BASIC_DATA.rows);
  const r = prepareChartV2All({ ...input, source: 'user', requestedChartType: 'bar' });
  assertOk(r.ok, `should succeed: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'bar');
  // B-10B: sourceColumns/sourceRows 应保存
  assertOk(Array.isArray(r.chart!.sourceColumns), 'sourceColumns 应存在');
  assertOk(Array.isArray(r.chart!.sourceRows), 'sourceRows 应存在');
  assertEqual(r.chart!.sourceColumns!.length, BASIC_DATA.columns.length);
  assertEqual(r.chart!.sourceRows!.length, BASIC_DATA.rows.length);
});

test('user: source=user + requestedChartType=gauge for single_kpi → 正确选择 gauge', () => {
  const r = prepareChartV2All({
    ...makeInput(['total_count'], [{ total_count: 342 }]),
    source: 'user',
    requestedChartType: 'gauge',
  });
  assertOk(r.ok, `should succeed: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'gauge');
  assertOk(Array.isArray(r.chart!.sourceColumns));
  assertEqual(r.chart!.sourceColumns![0], 'total_count');
});

test('user: source=user + requestedChartType=boxplot → 正确选择 boxplot', () => {
  const r = prepareChartV2All({
    ...makeInput(
      ['station', 'ph_value'],
      [
        { station: '站点A', ph_value: 7.2 },
        { station: '站点A', ph_value: 7.5 },
        { station: '站点B', ph_value: 8.1 },
        { station: '站点B', ph_value: 7.9 },
      ],
    ),
    source: 'user',
    requestedChartType: 'boxplot',
  });
  assertOk(r.ok, `should succeed: ${r.errorCode}`);
  assertEqual(r.chart!.spec.type, 'boxplot');
  // source 数据与 transform 后不同
  assertOk(r.chart!.sourceColumns!.includes('ph_value'),
    'sourceColumns 应含原始列 ph_value');
  assertOk(r.chart!.columns.includes('min'),
    'transform 后 columns 应含 min');
});

test('user: source=user + requestedChartType → 不可用时回退', () => {
  // 对 categorical data 请求 gauge → 应回退到 bar（supported）
  const r = prepareChartV2All({
    ...makeInput(BASIC_DATA.columns, BASIC_DATA.rows),
    source: 'user',
    requestedChartType: 'gauge',
  });
  assertOk(r.ok, '应成功（回退到支持的图表）');
  assertOk(r.planning.fallbackNotice !== null,
    '应有 fallbackNotice 说明 gauge 不可用');
  console.log(`  [info] gauge requested → got ${r.chart!.spec.type}, fallback: ${r.planning.fallbackNotice}`);
});

// ============================================================
// 5. 跨验证：Shadow Comparison 关键断言一致
// ============================================================

test('cross: ALL 的 gauge/scatter/bubble 与 PILOT 不冲突', () => {
  // ALL 扩展了 PILOT，但不影响 PILOT 已有的 bar/line 行为
  const catAll = prepareChartV2All(makeInput(BASIC_DATA.columns, BASIC_DATA.rows));
  const catPilot = prepareChartV2(makeInput(BASIC_DATA.columns, BASIC_DATA.rows));

  assertOk(catAll.ok && catPilot.ok, 'both should succeed for categorical data');
  assertEqual(catAll.chart!.spec.type, 'bar');
  assertEqual(catPilot.chart!.spec.type, 'bar');
  assertEqual(catAll.selectedPlan!.variantId, catPilot.selectedPlan!.variantId,
    'bar variant should be the same in ALL and PILOT');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Runtime Chart Path V2 Tests (B-5C)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
