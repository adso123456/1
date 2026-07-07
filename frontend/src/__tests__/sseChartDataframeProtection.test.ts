// sseChartDataframeProtection.test.ts — P2B: V2 chart 保护，防止 SSE dataframe 覆盖 transform 后 columns/rows
//
// 验证 refreshChartsFromDataframe 函数：
// 1. V2 chart（含 v2Meta）→ 跳过覆盖，保留 transform 后 columns/rows/spec/source 数据
// 2. 旧 chart（无 v2Meta）→ 继续用最新 dataframe 更新 columns/rows/dataVersion
// 3. 混合 charts 数组 → V2 不变，旧 chart 更新

import { refreshChartsFromDataframe } from '../hooks/useSSE.js';
import type { ChartData } from '../types.js';

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
// 工厂函数
// ============================================================

function makeV2Chart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'v2-chart',
    title: 'V2 Chart',
    dataVersion: 1,
    columns: ['region', 'count_sum'],           // transform 后
    rows: [{ region: '城北', count_sum: 25 }],  // transform 后
    spec: { type: 'bar', xField: 'region', yFields: ['count_sum'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_1' },
    sourceColumns: ['region', 'count'],
    sourceRows: [{ region: '城北', count: 10 }, { region: '城北', count: 15 }],
    ...overrides,
  };
}

function makeOldChart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'old-chart',
    title: 'Old Chart',
    dataVersion: 1,
    columns: ['old_a', 'old_b'],
    rows: [{ old_a: 1, old_b: 2 }],
    spec: { type: 'bar', xField: 'old_a', yFields: ['old_b'] },
    ...overrides,
  };
}

// ============================================================
// T1: V2 chart — 跳过覆盖
// ============================================================

test('T1a: V2 chart columns 不被 dataframe 覆盖', () => {
  const v2 = makeV2Chart();
  const newColumns = ['month', 'discharge'];
  const newRows = [{ month: '1月', discharge: 120 }];
  const newVersion = 5;

  const result = refreshChartsFromDataframe([v2], newColumns, newRows, newVersion);

  assertEqual(result.length, 1);
  assertEqual(result[0].columns, v2.columns, 'columns 不变');
  assertEqual(result[0].columns[0], 'region');
  assertEqual(result[0].columns[1], 'count_sum');
});

test('T1b: V2 chart rows 不被 dataframe 覆盖', () => {
  const v2 = makeV2Chart();
  const newColumns = ['month', 'discharge'];
  const newRows = [{ month: '1月', discharge: 120 }];
  const newVersion = 5;

  const result = refreshChartsFromDataframe([v2], newColumns, newRows, newVersion);

  assertEqual(result[0].rows, v2.rows, 'rows 不变');
  assertEqual(result[0].rows[0].count_sum, 25);
});

test('T1c: V2 chart spec 不受影响', () => {
  const v2 = makeV2Chart();
  const result = refreshChartsFromDataframe([v2], ['x'], [{ x: 1 }], 5);

  assertEqual(result[0].spec.type, 'bar');
  assertEqual(result[0].spec.xField, 'region');
  assertOk(Array.isArray(result[0].spec.yFields) && result[0].spec.yFields![0] === 'count_sum');
});

test('T1d: V2 chart v2Meta 不受影响', () => {
  const v2 = makeV2Chart();
  const result = refreshChartsFromDataframe([v2], ['x'], [{ x: 1 }], 5);

  assertOk(result[0].v2Meta !== undefined);
  assertEqual(result[0].v2Meta!.transform, 'group_by_sum');
  assertEqual(result[0].v2Meta!.semanticMode, 'comparison');
});

test('T1e: V2 chart sourceColumns/sourceRows 不受影响', () => {
  const v2 = makeV2Chart();
  const result = refreshChartsFromDataframe([v2], ['x'], [{ x: 1 }], 5);

  assertOk(Array.isArray(result[0].sourceColumns));
  assertEqual(result[0].sourceColumns![0], 'region');
  assertEqual(result[0].sourceColumns![1], 'count');
  assertOk(Array.isArray(result[0].sourceRows));
  assertEqual(result[0].sourceRows!.length, 2);
});

test('T1f: V2 chart dataVersion 不变', () => {
  const v2 = makeV2Chart({ dataVersion: 3 });
  const result = refreshChartsFromDataframe([v2], ['x'], [{ x: 1 }], 99);

  assertEqual(result[0].dataVersion, 3, 'dataVersion 应保持原值');
});

test('T1g: V2 chart explicitType 不变', () => {
  const v2 = makeV2Chart({ explicitType: true });
  const result = refreshChartsFromDataframe([v2], ['x'], [{ x: 1 }], 5);

  assertEqual(result[0].explicitType, true);
});

// ============================================================
// T2: 旧 chart（无 v2Meta）— 继续更新
// ============================================================

test('T2a: 旧 chart columns 更新为 dataframe columns', () => {
  const old = makeOldChart();
  const newColumns = ['new_a', 'new_b'];
  const newRows = [{ new_a: 10, new_b: 20 }];
  const newVersion = 10;

  const result = refreshChartsFromDataframe([old], newColumns, newRows, newVersion);

  assertEqual(result[0].columns, newColumns);
  assertEqual(result[0].columns[0], 'new_a');
});

test('T2b: 旧 chart rows 更新为 dataframe rows', () => {
  const old = makeOldChart();
  const newColumns = ['new_a', 'new_b'];
  const newRows = [{ new_a: 10, new_b: 20 }];
  const newVersion = 10;

  const result = refreshChartsFromDataframe([old], newColumns, newRows, newVersion);

  assertEqual(result[0].rows, newRows);
  assertEqual(result[0].rows[0].new_a, 10);
});

test('T2c: 旧 chart dataVersion 更新', () => {
  const old = makeOldChart({ dataVersion: 1 });
  const result = refreshChartsFromDataframe([old], ['a'], [{ a: 1 }], 42);

  assertEqual(result[0].dataVersion, 42);
});

// ============================================================
// T3: 混合数组 — V2 不变，旧 chart 更新
// ============================================================

test('T3a: 混合数组 V2 + 旧chart → V2 不变', () => {
  const v2 = makeV2Chart();
  const old = makeOldChart();
  const newColumns = ['x', 'y'];
  const newRows = [{ x: 1, y: 2 }];
  const newVersion = 7;

  const result = refreshChartsFromDataframe([v2, old], newColumns, newRows, newVersion);

  assertEqual(result.length, 2);
  // V2 不变
  assertEqual(result[0].columns, v2.columns);
  assertEqual(result[0].v2Meta!.transform, 'group_by_sum');
  // 旧 chart 更新
  assertEqual(result[1].columns, newColumns);
  assertEqual(result[1].dataVersion, 7);
});

test('T3b: 空 charts 数组 → 空数组', () => {
  const result = refreshChartsFromDataframe([], ['a'], [{ a: 1 }], 1);
  assertEqual(result.length, 0);
});

// ============================================================
// T4: 边界情况
// ============================================================

test('T4a: 仅 V2 charts 全部被保护', () => {
  const v2a = makeV2Chart({ id: 'v2-1' });
  const v2b = makeV2Chart({ id: 'v2-2', spec: { type: 'boxplot', xField: 'station', yFields: ['min', 'q1', 'median', 'q3', 'max'] }, v2Meta: { semanticMode: 'distribution', transform: 'boxplot_summary', archetype: 'categorical_series', variantId: 'boxplot_1' } });
  const newCols = ['random'];
  const newRows = [{ random: 0 }];
  const newVersion = 99;

  const result = refreshChartsFromDataframe([v2a, v2b], newCols, newRows, newVersion);

  assertEqual(result.length, 2);
  assertEqual(result[0].columns, v2a.columns);  // 不变
  assertEqual(result[1].columns, v2b.columns);  // 不变
  assertOk(result[1].spec.yFields!.includes('min'), 'boxplot yFields 保留');
});

test('T4b: V2 chart v2Meta 为 null/undefined → 视为旧 chart，更新', () => {
  // 如果 v2Meta 为 undefined（旧图表被标记为没有 v2Meta），应走旧逻辑
  const chartWithoutV2Meta: ChartData = {
    id: 'no-v2meta',
    title: 'No V2Meta',
    dataVersion: 1,
    columns: ['a'],
    rows: [{ a: 1 }],
    spec: { type: 'bar', xField: 'a', yFields: ['a'] },
    // v2Meta 未设置 → undefined
  };

  const newColumns = ['b'];
  const newRows = [{ b: 2 }];
  const result = refreshChartsFromDataframe([chartWithoutV2Meta], newColumns, newRows, 5);

  // 无 v2Meta → 应被更新
  assertEqual(result[0].columns, newColumns);
  assertEqual(result[0].dataVersion, 5);
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`SSE Chart DataFrame Protection Tests (P2B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
