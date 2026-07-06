// chartDescriptionV2.test.ts — V2 图表语义描述测试（B-8B）
//
// 验证 generateChartDescription 对 V2 ChartData（携带 v2Meta）产出正确描述。
// 旧路径（无 v2Meta）的回归测试也在本文件中。

import { generateChartDescription } from '../chartDescription.js';
import type { ChartData, ChartMetaV2 } from '../types.js';

let passed = 0;
let failed = 0;

function assertOk(cond: boolean, msg?: string): void {
  if (!cond) throw new Error(msg ?? `expected truthy, got ${JSON.stringify(cond)}`);
}

function assertNotNull<T>(v: T | null | undefined, msg?: string): asserts v is T {
  if (v === null || v === undefined) {
    throw new Error(msg ?? `expected non-null, got ${JSON.stringify(v)}`);
  }
}

function assertIncludes(haystack: string, needle: string, msg?: string): void {
  if (!haystack.includes(needle)) {
    throw new Error(msg ?? `expected "${haystack}" to include "${needle}"`);
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

// ---- 工厂函数 ----

function baseMeta(overrides?: Partial<ChartMetaV2>): ChartMetaV2 {
  return {
    semanticMode: 'comparison',
    transform: 'none',
    archetype: 'categorical_series',
    variantId: 'test_variant',
    ...overrides,
  };
}

function chart(overrides: Partial<ChartData>): ChartData {
  return {
    id: 'test',
    columns: ['product', 'sales'],
    rows: [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
      { product: 'C', sales: 150 },
    ],
    spec: { type: 'bar', xField: 'product', yFields: ['sales'] },
    title: 'Test',
    dataVersion: 1,
    ...overrides,
  };
}

// ============================================================
// 1. V2 bar + none → 分类描述
// ============================================================

test('V2 bar + none → 有分类描述', () => {
  const c = chart({
    v2Meta: baseMeta({ transform: 'none', semanticMode: 'comparison' }),
  });
  const desc = generateChartDescription(c, 'bar');
  assertNotNull(desc, 'description should not be null');
  assertIncludes(desc, 'product', 'should include xField label');
  assertIncludes(desc, 'sales', 'should include metric label');
});

// ============================================================
// 2. V2 bar + group_by_sum → 包含聚合说明
// ============================================================

test('V2 bar + group_by_sum → 描述包含聚合说明', () => {
  const c = chart({
    rows: [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ],
    v2Meta: baseMeta({ transform: 'group_by_sum', semanticMode: 'comparison' }),
  });
  const desc = generateChartDescription(c, 'bar');
  assertNotNull(desc, 'description should not be null');
  assertIncludes(desc, '求和', 'should mention 求和');
});

// ============================================================
// 3. V2 line → 有趋势描述
// ============================================================

test('V2 line → 有趋势描述', () => {
  const c = chart({
    columns: ['month', 'value'],
    rows: [
      { month: '1月', value: 100 },
      { month: '2月', value: 120 },
      { month: '3月', value: 110 },
    ],
    spec: { type: 'line', xField: 'month', yFields: ['value'] },
    v2Meta: baseMeta({ semanticMode: 'trend' }),
  });
  const desc = generateChartDescription(c, 'line');
  assertNotNull(desc, 'description should not be null');
  assertIncludes(desc, 'month', 'should include xField label');
  assertIncludes(desc, 'value', 'should include metric label');
});

// ============================================================
// 4. V2 gauge → 不为 null，包含单值含义
// ============================================================

test('V2 gauge → 不为 null，包含单值/KPI 含义', () => {
  const c = chart({
    columns: ['total_count'],
    rows: [{ total_count: 342 }],
    spec: { type: 'gauge', valueField: 'total_count' },
    v2Meta: baseMeta({ semanticMode: 'kpi' }),
  });
  const desc = generateChartDescription(c, 'gauge');
  assertNotNull(desc, 'gauge description should not be null');
  assertIncludes(desc, '仪表盘', 'should mention 仪表盘');
  assertIncludes(desc, '342', 'should include the value');
});

// ============================================================
// 5. V2 scatter → 不为 null，包含两个字段名
// ============================================================

test('V2 scatter → 不为 null，包含两个字段名', () => {
  const c = chart({
    columns: ['rainfall', 'runoff'],
    rows: Array.from({ length: 10 }, (_, i) => ({ rainfall: i, runoff: i * 2 })),
    spec: { type: 'scatter', xField: 'rainfall', yFields: ['runoff'] },
    v2Meta: baseMeta({ semanticMode: 'relationship' }),
  });
  const desc = generateChartDescription(c, 'scatter');
  assertNotNull(desc, 'scatter description should not be null');
  assertIncludes(desc, '散点图', 'should mention 散点图');
  assertIncludes(desc, 'rainfall', 'should include xField');
  assertIncludes(desc, 'runoff', 'should include yField');
});

// ============================================================
// 6. V2 bubble → 不为 null，包含 x/y/size 字段名
// ============================================================

test('V2 bubble → 不为 null，包含 x/y/size 字段名', () => {
  const c = chart({
    columns: ['rainfall', 'runoff', 'area'],
    rows: Array.from({ length: 8 }, (_, i) => ({ rainfall: i, runoff: i * 2, area: i * 10 })),
    spec: { type: 'bubble', xField: 'rainfall', yFields: ['runoff'], sizeField: 'area' },
    v2Meta: baseMeta({ semanticMode: 'relationship' }),
  });
  const desc = generateChartDescription(c, 'bubble');
  assertNotNull(desc, 'bubble description should not be null');
  assertIncludes(desc, '气泡图', 'should mention 气泡图');
  assertIncludes(desc, 'rainfall', 'should include xField');
  assertIncludes(desc, 'runoff', 'should include yField');
  assertIncludes(desc, 'area', 'should include sizeField');
});

// ============================================================
// 6b. V2 bubble without sizeField → 仍然有效
// ============================================================

test('V2 bubble without sizeField → 描述仍然有效', () => {
  const c = chart({
    columns: ['x', 'y'],
    rows: Array.from({ length: 5 }, (_, i) => ({ x: i, y: i * 2 })),
    spec: { type: 'bubble', xField: 'x', yFields: ['y'] },
    v2Meta: baseMeta({ semanticMode: 'relationship' }),
  });
  const desc = generateChartDescription(c, 'bubble');
  assertNotNull(desc, 'bubble description should not be null');
  assertIncludes(desc, '气泡图', 'should mention 气泡图');
});

// ============================================================
// 7. V2 boxplot + boxplot_summary → 不为 null，包含五数概括
// ============================================================

test('V2 boxplot + boxplot_summary → 不为 null，包含五数概括', () => {
  const c = chart({
    columns: ['station', 'min', 'q1', 'median', 'q3', 'max'],
    rows: [
      { station: 'A', min: 6.8, q1: 7.0, median: 7.2, q3: 7.5, max: 7.5 },
      { station: 'B', min: 7.4, q1: 7.9, median: 8.1, q3: 8.1, max: 8.1 },
    ],
    spec: { type: 'boxplot', xField: 'station', yFields: ['min', 'q1', 'median', 'q3', 'max'] },
    v2Meta: baseMeta({ transform: 'boxplot_summary', semanticMode: 'distribution' }),
  });
  const desc = generateChartDescription(c, 'boxplot');
  assertNotNull(desc, 'boxplot description should not be null');
  assertIncludes(desc, '箱线图', 'should mention 箱线图');
  assertIncludes(desc, '五数概括', 'should mention 五数概括');
  assertIncludes(desc, '站点', 'should include xField label (station→站点)');
});

// ============================================================
// 7b. V2 boxplot without boxplot_summary → 不抛异常
// ============================================================

test('V2 boxplot without boxplot_summary transform → 描述仍有效', () => {
  const c = chart({
    columns: ['station', 'min', 'q1', 'median', 'q3', 'max'],
    rows: [
      { station: 'A', min: 1, q1: 2, median: 3, q3: 4, max: 5 },
    ],
    spec: { type: 'boxplot', xField: 'station', yFields: ['min', 'q1', 'median', 'q3', 'max'] },
    v2Meta: baseMeta({ transform: 'none', semanticMode: 'distribution' }),
  });
  const desc = generateChartDescription(c, 'boxplot');
  assertNotNull(desc, 'boxplot description should not be null');
  assertIncludes(desc, '箱线图', 'should mention 箱线图');
});

// ============================================================
// 8. V2 heatmap + matrix_aggregate → 不为 null，包含矩阵含义
// ============================================================

test('V2 heatmap + matrix_aggregate → 不为 null，包含矩阵/热力图含义', () => {
  const c = chart({
    columns: ['region', 'month', 'avg_temp'],
    rows: [
      { region: '城北', month: '1月', avg_temp: 5.2 },
      { region: '城北', month: '2月', avg_temp: 7.1 },
      { region: '城南', month: '1月', avg_temp: 6.0 },
      { region: '城南', month: '2月', avg_temp: 8.3 },
    ],
    spec: { type: 'heatmap', xField: 'region', yFields: ['month'], valueField: 'avg_temp' },
    v2Meta: baseMeta({ transform: 'matrix_aggregate', semanticMode: 'distribution' }),
  });
  const desc = generateChartDescription(c, 'heatmap');
  assertNotNull(desc, 'heatmap description should not be null');
  assertIncludes(desc, '热力图', 'should mention 热力图');
  assertIncludes(desc, '地区', 'should include xField (region→地区)');
  assertIncludes(desc, 'month', 'should include yField (列)');
  assertIncludes(desc, '聚合', 'should mention 聚合 for matrix_aggregate');
});

// ============================================================
// 8b. V2 heatmap without valueField → 不抛异常
// ============================================================

test('V2 heatmap without valueField → 描述仍有效', () => {
  const c = chart({
    columns: ['x', 'y', 'v'],
    rows: [{ x: 'A', y: 'X', v: 10 }],
    spec: { type: 'heatmap', xField: 'x', yFields: ['y'], valueField: undefined },
    v2Meta: baseMeta({ transform: 'matrix_aggregate' }),
  });
  const desc = generateChartDescription(c, 'heatmap');
  assertNotNull(desc, 'heatmap description should not be null');
  assertIncludes(desc, '热力图', 'should mention 热力图');
});

// ============================================================
// 9. V2 radar → 不为 null，包含多指标/剖面
// ============================================================

test('V2 radar → 不为 null，包含多指标/剖面', () => {
  const c = chart({
    columns: ['station', 'ph', 'do', 'cod', 'nh3n'],
    rows: [
      { station: 'A', ph: 7.2, do: 6.5, cod: 12.0, nh3n: 0.5 },
      { station: 'B', ph: 7.8, do: 5.8, cod: 18.0, nh3n: 0.8 },
    ],
    spec: { type: 'radar', xField: 'station', yFields: ['ph', 'do', 'cod', 'nh3n'] },
    v2Meta: baseMeta({ semanticMode: 'profile' }),
  });
  const desc = generateChartDescription(c, 'radar');
  assertNotNull(desc, 'radar description should not be null');
  assertIncludes(desc, '雷达图', 'should mention 雷达图');
  assertIncludes(desc, '4', 'should include measure count');
});

// ============================================================
// 10. 旧路径（无 v2Meta）→ 行为不变
// ============================================================

test('旧路径 bar 无 v2Meta → 行为不变', () => {
  const c = chart({ v2Meta: undefined });
  const desc = generateChartDescription(c, 'bar');
  assertNotNull(desc, 'old path bar description should not be null');
  assertIncludes(desc, 'product', 'should include xField');
});

test('旧路径 line 无 v2Meta → 行为不变', () => {
  const c = chart({
    columns: ['month', 'value'],
    rows: [
      { month: '1月', value: 100 },
      { month: '2月', value: 120 },
    ],
    spec: { type: 'line', xField: 'month', yFields: ['value'] },
    v2Meta: undefined,
  });
  const desc = generateChartDescription(c, 'line');
  assertNotNull(desc, 'old path line description should not be null');
});

test('旧路径 pie 无 v2Meta → 行为不变', () => {
  const c = chart({
    spec: { type: 'pie', xField: 'product', yFields: ['sales'] },
    v2Meta: undefined,
  });
  const desc = generateChartDescription(c, 'pie');
  assertNotNull(desc, 'old path pie description should not be null');
});

test('旧路径 donut 无 v2Meta → 行为不变', () => {
  const c = chart({
    spec: { type: 'donut', xField: 'product', yFields: ['sales'] },
    v2Meta: undefined,
  });
  const desc = generateChartDescription(c, 'donut');
  assertNotNull(desc, 'old path donut description should not be null');
});

test('旧路径 combo 无 v2Meta → 行为不变', () => {
  const c = chart({
    columns: ['month', 'sales', 'profit'],
    rows: [
      { month: '1月', sales: 100, profit: 30 },
      { month: '2月', sales: 120, profit: 40 },
    ],
    spec: { type: 'combo', xField: 'month', yFields: ['sales', 'profit'] },
    v2Meta: undefined,
  });
  const desc = generateChartDescription(c, 'combo');
  assertNotNull(desc, 'old path combo description should not be null');
});

test('旧路径 scatter 无 v2Meta → 仍为 null（不受 V2 影响）', () => {
  const c = chart({
    columns: ['x', 'y'],
    rows: [{ x: 1, y: 2 }],
    spec: { type: 'scatter', xField: 'x', yFields: ['y'] },
    v2Meta: undefined,
  });
  const desc = generateChartDescription(c, 'scatter');
  // 旧路径 scatter 在 default case 返回 null
  assertOk(desc === null, 'old path scatter should still return null');
});

// ============================================================
// 11. 边界：v2Meta 存在但 spec 不完整 → 不抛异常
// ============================================================

test('V2 gauge with missing valueField → 安全返回 null', () => {
  const c = chart({
    spec: { type: 'gauge', xField: 'total' },  // 无 valueField
    v2Meta: baseMeta({ semanticMode: 'kpi' }),
  });
  // 不应抛异常（无 valueField 可能返回 null）
  generateChartDescription(c, 'gauge');
  assertOk(true, 'should not throw');
});

test('V2 scatter with missing yFields → 安全返回 null', () => {
  const c = chart({
    columns: ['x'],
    rows: [{ x: 1 }],
    spec: { type: 'scatter', xField: 'x' },
    v2Meta: baseMeta({ semanticMode: 'relationship' }),
  });
  const desc = generateChartDescription(c, 'scatter');
  assertOk(desc === null, 'should return null for missing yFields');
});

test('V2 heatmap with empty rows → 安全返回 null', () => {
  const c = chart({
    rows: [],
    spec: { type: 'heatmap', xField: 'x', yFields: ['y'] },
    v2Meta: baseMeta({ transform: 'matrix_aggregate' }),
  });
  const desc = generateChartDescription(c, 'heatmap');
  assertOk(desc === null, 'should return null for empty rows');
});

test('V2 with missing xField → 安全返回 null', () => {
  const c = chart({
    spec: { type: 'bar', xField: null as any },
    v2Meta: baseMeta(),
  });
  const desc = generateChartDescription(c, 'bar');
  assertOk(desc === null, 'should return null for missing xField');
});

// ============================================================
// V2 area / horizontal_bar / pie / donut / combo 覆盖
// ============================================================

test('V2 area → 有描述', () => {
  const c = chart({
    columns: ['month', 'discharge'],
    rows: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 130 },
    ],
    spec: { type: 'area', xField: 'month', yFields: ['discharge'] },
    v2Meta: baseMeta({ semanticMode: 'trend' }),
  });
  const desc = generateChartDescription(c, 'area');
  assertNotNull(desc, 'area description should not be null');
  assertIncludes(desc, 'month', 'should include xField');
});

test('V2 horizontal_bar + group_by_sum → 包含聚合说明', () => {
  const c = chart({
    rows: [
      { product: 'A', sales: 100 },
      { product: 'B', sales: 200 },
    ],
    v2Meta: baseMeta({ transform: 'group_by_sum' }),
  });
  const desc = generateChartDescription(c, 'horizontal_bar');
  assertNotNull(desc, 'horizontal_bar description should not be null');
  assertIncludes(desc, '求和', 'should mention 求和');
});

test('V2 pie → 有描述', () => {
  const c = chart({
    spec: { type: 'pie', xField: 'product', yFields: ['sales'] },
    v2Meta: baseMeta({ semanticMode: 'part_to_whole' }),
  });
  const desc = generateChartDescription(c, 'pie');
  assertNotNull(desc, 'pie description should not be null');
});

test('V2 donut → 有描述', () => {
  const c = chart({
    spec: { type: 'donut', xField: 'product', yFields: ['sales'] },
    v2Meta: baseMeta({ semanticMode: 'part_to_whole' }),
  });
  const desc = generateChartDescription(c, 'donut');
  assertNotNull(desc, 'donut description should not be null');
});

test('V2 combo → 有描述', () => {
  const c = chart({
    columns: ['month', 'sales', 'profit'],
    rows: [
      { month: '1月', sales: 100, profit: 30 },
      { month: '2月', sales: 120, profit: 40 },
    ],
    spec: { type: 'combo', xField: 'month', yFields: ['sales', 'profit'] },
    v2Meta: baseMeta({ semanticMode: 'profile' }),
  });
  const desc = generateChartDescription(c, 'combo');
  assertNotNull(desc, 'combo description should not be null');
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 ChartDescription Tests (B-8B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
