// chartPipelineV2.test.ts — V2 Pipeline 契约测试
//
// 验证 Planner → DataTransform → Renderer 全链路，不含 React/SSE。

import {
  prepareChartV2,
  type PrepareChartInputV2,
  type PrepareChartResultV2,
} from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';

let passed = 0;
let failed = 0;

// ---- 断言 ----

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

// ---- 数据集 ----

const CATEGORICAL_DATA = {
  columns: ['product', 'sales'] as string[],
  rows: [
    { product: 'A', sales: 100 },
    { product: 'B', sales: 200 },
    { product: 'C', sales: 150 },
  ] as Row[],
};

const TEMPORAL_DATA = {
  columns: ['year', 'revenue'] as string[],
  rows: [
    { year: '2020', revenue: 500 },
    { year: '2021', revenue: 600 },
    { year: '2022', revenue: 700 },
  ] as Row[],
};

/** 重复维度 + 可加指标 → 触发 bar_categorical_aggregated（gate 已翻转为 true） */
const DUPLICATE_ADDITIVE_DATA = {
  columns: ['region', 'total'] as string[],
  rows: [
    { region: '华东', total: 5 },
    { region: '华东', total: 3 },
    { region: '华北', total: 10 },
  ] as Row[],
};

const DETAIL_DUPLICATE_DATA = {
  columns: ['category', 'avg_score'] as string[],
  rows: [
    { category: 'A', avg_score: 85 },
    { category: 'A', avg_score: 90 },
    { category: 'B', avg_score: 75 },
    { category: 'B', avg_score: 80 },
  ] as Row[],
};

const MULTI_ENTITY_TEMPORAL_DATA = {
  columns: ['company', 'year', 'revenue'] as string[],
  rows: [
    { company: 'ACME', year: '2020', revenue: 100 },
    { company: 'ACME', year: '2021', revenue: 110 },
    { company: 'BETA', year: '2020', revenue: 200 },
    { company: 'BETA', year: '2021', revenue: 210 },
  ] as Row[],
};

// ---- 辅助 ----

function snap(input: PrepareChartInputV2): string {
  return JSON.stringify({
    columns: input.columns,
    rows: input.rows,
    source: input.source,
    intent: input.intent,
  });
}

function findPlan(result: PrepareChartResultV2, variantId: string) {
  return result.planning.plans.find(p => p.variantId === variantId);
}

// ============================================================
// 1. 分类数据 → bar + none → 成功
// ============================================================

test('categorical data → bar + none → success', () => {
  const input: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-1',
    title: 'Test 1',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.errorCode, null);
  assertOk(result.chart !== null, 'chart should not be null');
  assertEqual(result.chart!.spec.type, 'bar');
  assertEqual(result.chart!.id, 'test-1');
  assertEqual(result.chart!.title, 'Test 1');
  assertEqual(result.chart!.dataVersion, 1);

  // ChartData 来自 transform 结果
  assertEqual(result.chart!.columns, result.transformResult!.columns);
  assertEqual(result.chart!.rows, result.transformResult!.rows);
  assertEqual(result.chart!.spec, result.transformResult!.spec);
});

// ============================================================
// 2. 时间数据 → line + none → 成功
// ============================================================

test('temporal data → line + none → success', () => {
  const input: PrepareChartInputV2 = {
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-2',
    title: 'Test 2',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'line');
  assertEqual(result.selectedPlan!.variantId, 'line_temporal_trend_single');
});

// ============================================================
// 3. 重复可加 → aggregated bar + group_by_sum → 成功
// ============================================================

test('duplicate additive data → aggregated bar + group_by_sum → success', () => {
  const input: PrepareChartInputV2 = {
    columns: DUPLICATE_ADDITIVE_DATA.columns,
    rows: DUPLICATE_ADDITIVE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-3',
    title: 'Test 3',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.selectedPlan!.variantId, 'bar_categorical_aggregated');
  assertEqual(result.selectedPlan!.transform, 'group_by_sum');
  assertOk(result.transformResult!.ok, 'transform should succeed');
  assertEqual(result.chart!.spec.type, 'bar');
});

// ============================================================
// 4. aggregated bar 输出只保留聚合行
// ============================================================

test('aggregated bar: output rows are aggregated, not raw', () => {
  const input: PrepareChartInputV2 = {
    columns: DUPLICATE_ADDITIVE_DATA.columns,
    rows: DUPLICATE_ADDITIVE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-4',
    title: 'Test 4',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);
  assertOk(result.ok);

  // 输入 3 行 → 输出应为 2 行（华东聚合为 1 行，华北 1 行）
  assertEqual(input.rows.length, 3, 'input should have 3 rows');
  assertOk(
    result.chart!.rows.length === 2,
    `expected 2 aggregated rows, got ${result.chart!.rows.length}`,
  );

  // 华东 total 应为 5+3=8
  const huadong = result.chart!.rows.find(r => r.region === '华东')!;
  assertEqual(huadong.total, 8, '华东 total should be 5+3=8');

  // 华北 total 应为 10
  const huabei = result.chart!.rows.find(r => r.region === '华北')!;
  assertEqual(huabei.total, 10, '华北 total should be 10');
});

// ============================================================
// 5. aggregated bar 能生成非空 ECharts option
// ============================================================

test('aggregated bar: renderer produces non-null option', () => {
  const input: PrepareChartInputV2 = {
    columns: DUPLICATE_ADDITIVE_DATA.columns,
    rows: DUPLICATE_ADDITIVE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-5',
    title: 'Test 5',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  assertOk(result.ok, `pipeline should succeed, got: ${result.errorCode}`);
  // renderer 验证通过意味着 buildChartOption 返回了非 null
  assertOk(result.chart !== null);
  assertEqual(result.errorCode, null);
});

// ============================================================
// 6. boxplot 仍被 gate 阻止
// ============================================================

test('boxplot variant still blocked by renderer gate', () => {
  const input: PrepareChartInputV2 = {
    columns: DETAIL_DUPLICATE_DATA.columns,
    rows: DETAIL_DUPLICATE_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-6',
    title: 'Test 6',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  const boxplot = findPlan(result, 'boxplot_grouped_distribution');
  assertOk(boxplot !== undefined, 'boxplot variant should exist in plans');
  assertEqual(
    boxplot!.resolvedSuitability,
    'unsupported',
    'boxplot should be unsupported',
  );
  assertEqual(boxplot!.spec, null, 'boxplot spec should be null');
  // gate 未翻转 → currentlySupported=false
});

// ============================================================
// 7. multi-series line 仍被 gate 阻止
// ============================================================

test('multi-series line variant still blocked by renderer gate', () => {
  const input: PrepareChartInputV2 = {
    columns: MULTI_ENTITY_TEMPORAL_DATA.columns,
    rows: MULTI_ENTITY_TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-7',
    title: 'Test 7',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  const multi = findPlan(result, 'line_temporal_trend_multi');
  assertOk(multi !== undefined, 'multi-series line variant should exist');
  assertEqual(
    multi!.resolvedSuitability,
    'unsupported',
    'multi-series line should be unsupported',
  );
  assertEqual(multi!.spec, null, 'multi-series line spec should be null');
});

// ============================================================
// 8. auto 无推荐 → no_default_plan
// ============================================================

test('auto with no recommended → no_default_plan', () => {
  // TEMPORAL_DATA + intent='distribution' 使 line(trend) 和 bar(comparison) 都降级
  const input: PrepareChartInputV2 = {
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'distribution',
    id: 'test-8',
    title: 'Test 8',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_default_plan');
  assertEqual(result.chart, null);
  assertOk(result.planning.noChartReason !== null);
});

// ============================================================
// 9. Renderer 拒绝 → renderer_rejected_plan
// ============================================================

test('categorical line: renderer rejects non-explicit categorical line', () => {
  // CATEGORICAL_DATA + user selects line → planner picks line_categorical_comparison
  // but buildAxisChart in line mode requires explicitType or temporal xField
  const input: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'line',
    id: 'test-9',
    title: 'Test 9',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);

  // selectedPlan 存在但 renderer 拒绝
  assertOk(result.selectedPlan !== null, 'should have selected plan');
  assertEqual(result.selectedPlan!.variantId, 'line_categorical_comparison');
  assertOk(result.transformResult!.ok, 'transform should succeed');
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'renderer_rejected_plan');
  assertEqual(result.chart, null);
});

// ============================================================
// 10. 输入不被修改
// ============================================================

test('pipeline does not mutate input', () => {
  const input: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-10',
    title: 'Test 10',
    dataVersion: 1,
  };
  const snapBefore = snap(input);

  prepareChartV2(input);

  assertEqual(snap(input), snapBefore, 'input should be unchanged');
});

// ============================================================
// 11. 成功 ChartData 全部来自 transformResult
// ============================================================

test('successful ChartData: columns/rows/spec are from transformResult', () => {
  const input: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-11',
    title: 'Test 11',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);
  assertOk(result.ok);

  // 引用相等：ChartData 直接使用 transformResult 的输出
  assertOk(
    result.chart!.columns === result.transformResult!.columns,
    'chart.columns should be transformResult.columns (same reference)',
  );
  assertOk(
    result.chart!.rows === result.transformResult!.rows,
    'chart.rows should be transformResult.rows (same reference)',
  );
  assertOk(
    result.chart!.spec === result.transformResult!.spec,
    'chart.spec should be transformResult.spec (same reference)',
  );
});

// ============================================================
// 12. 不混用原始 rows 与转换 spec
// ============================================================

test('ChartData does not mix raw rows with transformed spec', () => {
  const input: PrepareChartInputV2 = {
    columns: DUPLICATE_ADDITIVE_DATA.columns,
    rows: DUPLICATE_ADDITIVE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-12',
    title: 'Test 12',
    dataVersion: 1,
  };

  const result = prepareChartV2(input);
  assertOk(result.ok);

  // transform 前 spec 可能有 valueField，transform 后 spec 已调整
  // 此处验证 chart.rows 不包含原始输入的引用
  for (const row of result.chart!.rows) {
    assertOk(
      !input.rows.includes(row),
      'chart rows should not contain references to input rows',
    );
  }
  // chart.rows 应与 transformResult.rows 相同引用
  assertOk(
    result.chart!.rows === result.transformResult!.rows,
    'chart.rows should reference transformResult.rows',
  );
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 ChartPipeline Pilot Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
