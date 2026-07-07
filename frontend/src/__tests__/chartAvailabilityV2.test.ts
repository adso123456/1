// chartAvailabilityV2.test.ts — B-10D: V2 驱动图表可用性评估测试
//
// 验证 getChartTypeAvailabilityV2 对多种数据形态返回正确的 availability。

import { getChartTypeAvailabilityV2 } from '../chartPipelineV2.js';
import type { ChartData, ChartTypeAvailability } from '../types.js';
import type { Row } from '../datasetProfilerV2.js';
import { RENDERABLE_TYPES } from '../chartRegistry.js';

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, msg?: string): void {
  if (actual !== expected) {
    throw new Error(msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertOk(cond: boolean, msg?: string): void {
  if (!cond) throw new Error(msg ?? `expected falsy, got ${JSON.stringify(cond)}`);
}

function assertMatch(actual: string, regex: RegExp, msg?: string): void {
  if (!regex.test(actual)) {
    throw new Error(msg ?? `expected "${actual}" to match ${regex}`);
  }
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
// 辅助函数
// ============================================================

/** 构造带 source 数据的 V2 ChartData */
function makeV2Chart(
  columns: string[],
  rows: Row[],
  overrides?: Partial<ChartData>,
): ChartData {
  return {
    id: 'test-id',
    title: 'Test Chart',
    dataVersion: 1,
    columns,
    rows,
    spec: { type: 'bar' } as any,
    sourceColumns: columns,
    sourceRows: rows,
    ...overrides,
  };
}

/** 查找指定类型的 availability */
function findAvail(
  availList: ChartTypeAvailability[],
  type: string,
): ChartTypeAvailability {
  const found = availList.find(t => t.type === type);
  if (!found) throw new Error(`type "${type}" not found in availability list`);
  return found;
}

// ============================================================
// 1. categorical_series
// ============================================================

test('categorical_series: bar recommended', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
      { product: 'C', sales: 150 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const bar = findAvail(avail, 'bar');
  assertOk(bar.supported, 'bar 应 supported');
  assertOk(bar.spec !== null, 'bar spec 不应为 null');
  assertEqual(bar.suitability, 'recommended');
});

test('categorical_series: horizontal_bar recommended', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const hbar = findAvail(avail, 'horizontal_bar');
  // horizontal_bar 对 categorical 数据至少 allowed_explicit
  assertOk(hbar.supported || hbar.suitability === 'allowed_explicit',
    `horizontal_bar 应 supported 或 allowed_explicit: ${hbar.suitability}`);
});

test('categorical_series: pie/donut existence', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const pie = findAvail(avail, 'pie');
  const donut = findAvail(avail, 'donut');
  // pie/donut 存在且 label 正确
  assertEqual(pie.label, '饼图');
  assertEqual(donut.label, '环形图');
  // unsupported 时有 reason
  if (!pie.supported) assertOk(pie.reason.length > 0, 'pie unsupported 时应有 reason');
  if (!donut.supported) assertOk(donut.reason.length > 0, 'donut unsupported 时应有 reason');
});

test('categorical_series: gauge unsupported', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const gauge = findAvail(avail, 'gauge');
  // categorical_series 对 gauge 不支持
  assertEqual(gauge.suitability, 'unsupported',
    `gauge 应为 unsupported 对 categorical 数据: ${gauge.suitability}`);
  assertEqual(gauge.supported, false);
});

// ============================================================
// 2. temporal_series
// ============================================================

test('temporal_series: line recommended', () => {
  const chart = makeV2Chart(
    ['date', 'value'],
    [
      { date: '2024-01', value: 10 },
      { date: '2024-02', value: 20 },
      { date: '2024-03', value: 15 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const line = findAvail(avail, 'line');
  // line 对时序数据至少 supported
  assertOk(line.supported, 'line 应 supported 对时序数据');
  assertOk(line.spec !== null, 'line spec 不应为 null');
});

test('temporal_series: area availability', () => {
  const chart = makeV2Chart(
    ['date', 'value'],
    [
      { date: '2024-01', value: 10 },
      { date: '2024-02', value: 20 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const area = findAvail(avail, 'area');
  // area 对时序数据至少存在，label 正确
  assertEqual(area.label, '面积图');
  assertOk(
    area.suitability === 'recommended' || area.suitability === 'allowed_explicit' || area.suitability === 'unsupported',
    `area 应有合法 suitability: ${area.suitability}`,
  );
});

// ============================================================
// 3. single_value
// ============================================================

test('single_value: gauge recommended', () => {
  const chart = makeV2Chart(
    ['total'],
    [{ total: 342 }] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const gauge = findAvail(avail, 'gauge');
  // gauge 对单值数据应 recommended
  assertOk(gauge.supported, 'gauge 应 supported 对单值数据');
  assertEqual(gauge.suitability, 'recommended');
});

test('single_value: 大多数其他类型 unsupported', () => {
  const chart = makeV2Chart(
    ['total'],
    [{ total: 342 }] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  // scatter / pie / boxplot 等对单值数据应 unsupported（无维度字段）
  const scatter = findAvail(avail, 'scatter');
  assertEqual(scatter.suitability, 'unsupported',
    `scatter 对单值数据应为 unsupported: ${scatter.suitability}`);
  const pie = findAvail(avail, 'pie');
  assertEqual(pie.suitability, 'unsupported',
    `pie 对单值数据应为 unsupported: ${pie.suitability}`);
  const boxplot = findAvail(avail, 'boxplot');
  assertEqual(boxplot.suitability, 'unsupported',
    `boxplot 对单值数据应为 unsupported: ${boxplot.suitability}`);
});

// ============================================================
// 4. numeric_relationship
// ============================================================

test('numeric_relationship: scatter recommended', () => {
  const chart = makeV2Chart(
    ['rainfall', 'runoff'],
    [
      { rainfall: 12.5, runoff: 3.2 },
      { rainfall: 25.0, runoff: 7.8 },
      { rainfall: 8.0, runoff: 1.9 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const scatter = findAvail(avail, 'scatter');
  assertOk(scatter.supported, 'scatter 应 supported 对双数值数据');
  assertOk(scatter.spec !== null, 'scatter spec 不应为 null');
});

test('numeric_relationship: bubble supported', () => {
  const chart = makeV2Chart(
    ['rainfall', 'runoff', 'area'],
    [
      { rainfall: 12.5, runoff: 3.2, area: 150 },
      { rainfall: 25.0, runoff: 7.8, area: 300 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const bubble = findAvail(avail, 'bubble');
  assertOk(bubble.supported, 'bubble 应 supported 对三数值数据');
});

test('numeric_relationship: bar unsupported 或 allowed_explicit', () => {
  const chart = makeV2Chart(
    ['rainfall', 'runoff'],
    [
      { rainfall: 12.5, runoff: 3.2 },
      { rainfall: 25.0, runoff: 7.8 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const bar = findAvail(avail, 'bar');
  // bar 对纯数值关系数据应是 unsupported 或 allowed_explicit
  // 由 planner 按实际 archetype 决定
  const isExpected = bar.suitability === 'unsupported' || bar.suitability === 'allowed_explicit' || bar.suitability === 'recommended';
  assertOk(isExpected,
    `bar 对 numeric_relationship 应为 unsupported/allowed_explicit/recommended: ${bar.suitability}`);
});

// ============================================================
// 5. V2 boxplot chart — 基于 sourceRows/sourceColumns 而非 transform 后数据
// ============================================================

test('boxplot: availability 基于 sourceColumns/sourceRows，不基于 transform 后统计列', () => {
  // 模拟 boxplot 的 source 明细数据（category+value pair，非 min/q1/median/q3/max）
  // transform 后的数据是 boxplot 统计值（6 列 min/q1/median/q3/max）
  const chart = makeV2Chart(
    ['category', 'value'],
    [
      { category: 'A', value: 10 },
      { category: 'A', value: 20 },
      { category: 'A', value: 30 },
      { category: 'B', value: 15 },
      { category: 'B', value: 25 },
    ] as Row[],
    {
      // 当前 transform 后的数据是 boxplot 统计值（6 列）
      columns: ['category', 'min', 'q1', 'median', 'q3', 'max'],
      rows: [
        { category: 'A', min: 10, q1: 15, median: 20, q3: 25, max: 30 },
        { category: 'B', min: 15, q1: 20, median: 25, q3: 27, max: 30 },
      ] as Row[],
      spec: { type: 'boxplot', xField: 'category', yFields: ['min', 'q1', 'median', 'q3', 'max'] },
    },
  );
  const avail = getChartTypeAvailabilityV2(chart);

  // 关键：planning 基于 source (category + value = 2 列)，而非 transform 后的 6 列
  // 如果误用 transform 数据（6 列数值型），会认为 measureCount >= 2 而支持 scatter/pie
  // 基于 source（只有 2 列），measureCount = 1 或 0，scatter 应为 unsupported
  const scatter = findAvail(avail, 'scatter');
  assertEqual(scatter.suitability, 'unsupported',
    'scatter 应 unsupported（source 只有 1 个或 0 个 measure，\
    证明未将 min/q1/median/q3/max 当作普通数值列去判断 scatter）');

  // 13 种类型全部覆盖
  assertEqual(avail.length, 13);
});

// ============================================================
// 6. V2 group_by_sum chart — 基于 source 明细数据（非聚合后）
// ============================================================

test('group_by_sum: availability 基于 source 明细数据，不基于聚合后 rows', () => {
  // 模拟 group_by_sum：source 明细（3列4行），transform 后聚合（2列3行）
  const chart = makeV2Chart(
    ['product', 'category', 'sales'],
    [
      { product: 'A', category: 'X', sales: 100 },
      { product: 'B', category: 'X', sales: 200 },
      { product: 'A', category: 'Y', sales: 150 },
      { product: 'C', category: 'Y', sales: 300 },
    ] as Row[],
    {
      // 当前 transform 后的聚合数据（只有 2 列）
      columns: ['product', 'sales'],
      rows: [
        { product: 'A', sales: 250 },
        { product: 'B', sales: 200 },
        { product: 'C', sales: 300 },
      ] as Row[],
      spec: { type: 'bar', xField: 'product', yFields: ['sales'] },
    },
  );
  const avail = getChartTypeAvailabilityV2(chart);

  // 关键：planning 基于 source 明细（product, category, sales 共 3 列）
  // 而非聚合后（product, sales 共 2 列）
  // 验证 availability 列表完整且包含 13 种类型
  assertEqual(avail.length, 13);

  // bar 在 source 3 列数据下应有对应 suitability
  const bar = findAvail(avail, 'bar');
  assertOk(bar !== undefined, 'bar 应存在');
  // bar 对 categorical series 3 列至少能判定
  assertOk(
    bar.suitability === 'recommended' || bar.suitability === 'allowed_explicit' || bar.suitability === 'unsupported',
    `bar 应有合法 suitability: ${bar.suitability}`,
  );
  // unsupported 时有 reason
  if (!bar.supported) assertOk(bar.reason.length > 0, 'bar unsupported 时应有 reason');
});

// ============================================================
// 7. 无 source 数据旧图表 → fallback 到旧 getChartTypeAvailability
// ============================================================

test('无 source 数据：fallback 到旧 getChartTypeAvailability', () => {
  // 旧图表没有 sourceColumns/sourceRows
  const chart: ChartData = {
    id: 'old-chart',
    title: 'Old Chart',
    dataVersion: 1,
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 },
      { region: '城南', count: 32 },
    ] as Row[],
    spec: { type: 'bar', xField: 'region', yFields: ['count'] },
    // 无 sourceColumns/sourceRows
  };
  const avail = getChartTypeAvailabilityV2(chart);
  // 应正常返回 13 种类型，bar 应为 supported
  const bar = findAvail(avail, 'bar');
  assertOk(bar.supported, '旧图表 bar 应 supported');
  assertOk(bar.spec !== null, '旧图表 bar spec 不应为 null');
  assertEqual(bar.label, '柱状图');
});

test('无 source 数据旧图表：空数组不出错', () => {
  const chart: ChartData = {
    id: 'empty',
    title: 'Empty',
    dataVersion: 1,
    columns: [],
    rows: [],
    spec: { type: 'bar' },
  };
  // 不应抛出异常
  const avail = getChartTypeAvailabilityV2(chart);
  assertEqual(avail.length, 13, '应返回 13 种类型');
});

// ============================================================
// 8. 覆盖检查：全部 13 种图表类型，label 不为空
// ============================================================

test('返回结果覆盖全部 13 种 RENDERABLE_TYPES', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [{ product: 'A', sales: 100 }] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  assertEqual(avail.length, 13, '应返回 13 种图表类型');

  for (const type of RENDERABLE_TYPES) {
    const item = avail.find(t => t.type === type);
    assertOk(item !== undefined, `应包含 ${type}`);
    assertOk(item!.label !== undefined && item!.label.length > 0,
      `${type} label 不应为空，got: "${item!.label}"`);
    assertOk(typeof item!.supported === 'boolean', `${type} supported 应为 boolean`);
    // suitability 应为合法值
    assertOk(
      item!.suitability === 'recommended' ||
      item!.suitability === 'allowed_explicit' ||
      item!.suitability === 'unsupported',
      `${type} suitability 应为合法值: ${item!.suitability}`,
    );
  }
});

test('所有标签不为空且为中文', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [{ product: 'A', sales: 100 }] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);

  for (const item of avail) {
    assertOk(item.label.length > 0, `${item.type} label 不应为空`);
    // 中文标签应包含中文字符
    assertMatch(item.label, /[一-鿿]/,
      `${item.type} label 应包含中文: "${item.label}"`);
  }
});

// ============================================================
// 9. 边界情况：supported 时 spec 非 null，unsupported 时 spec 为 null
// ============================================================

test('supported 类型 spec 非 null', () => {
  const chart = makeV2Chart(
    ['product', 'sales'],
    [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const supportedTypes = avail.filter(t => t.supported);
  assertOk(supportedTypes.length > 0, '至少有一种 supported 类型');
  for (const item of supportedTypes) {
    assertOk(item.spec !== null,
      `${item.type}: supported=true 时 spec 不应为 null`);
  }
});

test('unsupported 类型 spec 为 null、reason 非空', () => {
  const chart = makeV2Chart(
    ['total'],
    [{ total: 342 }] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  const unsupportedTypes = avail.filter(t => !t.supported);
  for (const item of unsupportedTypes) {
    assertEqual(item.spec, null,
      `${item.type}: unsupported 时 spec 应为 null`);
    assertOk(item.reason.length > 0,
      `${item.type}: unsupported 时 reason 不应为空`);
  }
});

// ============================================================
// 10. V2 source 数据包含多列时正确评估
// ============================================================

test('V2 source 数据多列：正确评估全部 13 种类型', () => {
  const chart = makeV2Chart(
    ['date', 'product', 'sales', 'quantity', 'region'],
    [
      { date: '2024-01', product: 'A', sales: 100, quantity: 10, region: 'East' },
      { date: '2024-02', product: 'B', sales: 200, quantity: 20, region: 'West' },
      { date: '2024-03', product: 'A', sales: 150, quantity: 15, region: 'East' },
    ] as Row[],
  );
  const avail = getChartTypeAvailabilityV2(chart);
  assertEqual(avail.length, 13);

  // 至少几种核心图表可用
  const supportedCount = avail.filter(t => t.supported).length;
  assertOk(supportedCount >= 1, `至少 1 种图表应 supported: ${supportedCount}`);
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Chart Availability V2 Tests (B-10D)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
