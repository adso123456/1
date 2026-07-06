// chartViewSpecPreservation.test.ts — B-6B: ChartView spec 保留逻辑测试
//
// 测试 ChartView 中 activeSpec 选择逻辑（纯函数版）：
// explicitType=true 且 chart.spec.type === localType 时优先使用 chart.spec，
// 不被旧 getChartTypeAvailability() 推导的 spec 覆盖。

import { getChartTypeAvailability } from '../chartRegistry.js';
import { prepareChartV2All } from '../chartPipelineV2.js';
import type { ChartData, ChartSpec, RenderableChartType } from '../types.js';
import type { Row } from '../datasetProfilerV2.js';

// ============================================================
// 被测函数：模拟 ChartView 中 activeSpec 的选择逻辑
// ============================================================

/**
 * 模拟 ChartView.tsx 中 activeSpec 的选择逻辑，与组件保持同步。
 * 提取为纯函数以便测试，避免引入 React 测试框架。
 */
function selectActiveSpec(
  chart: ChartData,
  localType: RenderableChartType,
): ChartSpec | null {
  const allTypes = getChartTypeAvailability(chart);

  // ── 与 ChartView.tsx activeSpec useMemo 保持一致 ──
  // V2 auto 输出的 spec 优先保留，不被旧 availability 推导覆盖
  if (chart.explicitType === true && chart.spec?.type === localType) {
    return chart.spec;
  }

  const item = allTypes.find(t => t.type === localType);
  return item?.spec ?? null;
}

// ============================================================
// 断言工具
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
// 测试数据
// ============================================================

/** 模拟 prepareChartV2All 产出的 V2 ChartData（bar 示例） */
function makeV2BarChart(): ChartData {
  return {
    id: 'v2-test',
    title: 'V2 Test',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
      { region: '城东', count: 28 },
    ] as Row[],
    spec: {
      type: 'bar',
      xField: 'region',
      yFields: ['count'],
    },
    explicitType: true,
  };
}

// ============================================================
// 1. explicitType=true + 类型匹配 → 使用原始 chart.spec
// ============================================================

test('T1: explicitType=true + 类型匹配 → 保留原始 spec', () => {
  const chart = makeV2BarChart();
  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null, 'spec 不应为 null');
  assertEqual(spec!.type, 'bar');
  assertEqual(spec!.xField, 'region', '应保留原始 spec 的 xField');
});

test('T2: V2 spec 字段不被旧 availability 覆盖', () => {
  // 构造 V2 选了 quantity 的场景，确认不被旧逻辑覆盖为 sales
  const chart: ChartData = {
    id: 'v2-custom',
    title: 'Custom V2',
    dataVersion: 1,
    columns: ['product', 'sales', 'quantity'],
    rows: [
      { product: 'A', sales: 100, quantity: 50 },
      { product: 'B', sales: 200, quantity: 80 },
    ] as Row[],
    spec: {
      type: 'bar',
      xField: 'product',
      yFields: ['quantity'], // V2 选了 quantity
    },
    explicitType: true,
  };

  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null);
  assertEqual(spec!.yFields![0], 'quantity',
    '应保留 V2 选择的 quantity，不被旧逻辑覆盖为 sales');
});

// ============================================================
// 2. 用户切换图表类型 → localType !== chart.spec.type → 使用 availability
// ============================================================

test('T3: 用户切换到 line → 使用 availability spec', () => {
  const chart = makeV2BarChart(); // spec.type = 'bar'
  const spec = selectActiveSpec(chart, 'line');
  assertOk(spec !== null, 'line 应对该数据可用');
  assertEqual(spec!.type, 'line');
});

test('T4: 用户切换后 spec 来自 availability 而非原始 chart.spec', () => {
  const chart = makeV2BarChart();
  const spec = selectActiveSpec(chart, 'horizontal_bar');
  assertOk(spec !== null);
  assertEqual(spec!.type, 'horizontal_bar');
});

// ============================================================
// 3. 非 explicitType → 始终使用 availability spec
// ============================================================

test('T5: explicitType 未设置 → 使用 availability spec', () => {
  const chart: ChartData = {
    id: 'no-explicit',
    title: 'No Explicit',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
    ] as Row[],
    spec: { type: 'bar', xField: 'region', yFields: ['count'] },
    // explicitType 未设置
  };

  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null);
  // 不对 explicitType 做任何假设 — 只要不是 true，就走 availability
});

test('T6: explicitType=false → 使用 availability spec', () => {
  // 至少 2 行不同分类数据，确保旧 availability 判定 bar 为 supported
  const chart: ChartData = {
    id: 'explicit-false',
    title: 'Explicit False',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城东', count: 28 },
    ] as Row[],
    spec: { type: 'bar', xField: 'region', yFields: ['count'] },
    explicitType: false,
  };

  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null, 'availability 应对该数据返回 supported bar spec');
});

// ============================================================
// 4. B-5B 关键路径：V2 auto 产出的 scatter / bubble / gauge 保留
// ============================================================

test('T7: prepareChartV2All → scatter spec 保留', () => {
  const result = prepareChartV2All({
    columns: ['rainfall', 'runoff'],
    rows: [
      { rainfall: 12.5, runoff: 3.2 },
      { rainfall: 25.0, runoff: 7.8 },
      { rainfall: 8.0, runoff: 1.9 },
    ] as Row[],
    source: 'auto',
    intent: 'auto',
    id: 't7',
    title: 'Scatter Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  assertEqual(result.chart!.explicitType, true);
  assertEqual(result.chart!.spec.type, 'scatter');

  const spec = selectActiveSpec(result.chart!, 'scatter');
  assertOk(spec !== null);
  assertEqual(spec!.type, 'scatter');
  assertEqual(spec!.xField, 'rainfall');
  assertEqual(spec!.yFields![0], 'runoff');
});

test('T8: prepareChartV2All → bubble spec 保留', () => {
  const result = prepareChartV2All({
    columns: ['rainfall', 'runoff', 'area'],
    rows: [
      { rainfall: 12.5, runoff: 3.2, area: 150 },
      { rainfall: 25.0, runoff: 7.8, area: 300 },
      { rainfall: 8.0, runoff: 1.9, area: 100 },
    ] as Row[],
    source: 'auto',
    intent: 'auto',
    id: 't8',
    title: 'Bubble Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  assertEqual(result.chart!.explicitType, true);
  assertEqual(result.chart!.spec.type, 'bubble');

  const spec = selectActiveSpec(result.chart!, 'bubble');
  assertOk(spec !== null);
  assertEqual(spec!.type, 'bubble');
  assertEqual(spec!.xField, 'rainfall');
  assertEqual(spec!.yFields![0], 'runoff');
  assertEqual(spec!.sizeField, 'area');
});

test('T9: prepareChartV2All → gauge spec 保留', () => {
  const result = prepareChartV2All({
    columns: ['total_count'],
    rows: [{ total_count: 342 }] as Row[],
    source: 'auto',
    intent: 'auto',
    id: 't9',
    title: 'Gauge Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `prepareChartV2All 应成功: ${result.errorCode}`);
  assertEqual(result.chart!.explicitType, true);
  assertEqual(result.chart!.spec.type, 'gauge');

  const spec = selectActiveSpec(result.chart!, 'gauge');
  assertOk(spec !== null);
  assertEqual(spec!.type, 'gauge');
  assertEqual(spec!.valueField, 'total_count');
});

// ============================================================
// 5. 边界情况
// ============================================================

test('T10: chart.spec.type 与 localType 不同 → 回退到 availability', () => {
  // spec.type='line' ≠ localType='bar' → 条件不成立，走 availability
  const chart: ChartData = {
    id: 'different-type',
    title: 'Different Type',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
    ] as Row[],
    spec: { type: 'line', xField: 'region', yFields: ['count'] },
    explicitType: true,
  };

  // chart.spec?.type='line' !== localType='bar' → 回退到 availability
  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null, '应回退到 availability spec');
  assertEqual(spec!.type, 'bar', '应返回 availability 的 bar spec，而非原始 line spec');
});

test('T11: explicitType=true 但 availability 中该类型 unsupported → 仍使用原始 spec', () => {
  // 旧 getChartTypeAvailability 对重复 key 数据可能判 bar 为 unsupported
  // 但 explicitType=true 时应忽略 availability 判断，直接信任 V2 spec
  const chart: ChartData = {
    id: 'edge-dup',
    title: 'Edge',
    dataVersion: 1,
    columns: ['product', 'sales'],
    rows: [
      { product: 'A', sales: 100 },
      { product: 'A', sales: 200 },
      { product: 'B', sales: 150 },
    ] as Row[],
    spec: {
      type: 'bar',
      xField: 'product',
      yFields: ['sales'],
    },
    explicitType: true,
  };

  const spec = selectActiveSpec(chart, 'bar');
  assertOk(spec !== null, '即使旧逻辑判为 unsupported，仍应保留 V2 spec');
  assertEqual(spec!.xField, 'product');
  assertEqual(spec!.yFields![0], 'sales');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`ChartView Spec Preservation Tests (B-6B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
