// chartPipelineV2.test.ts — V2 Pipeline 契约测试
//
// 验证 Planner → DataTransform → Renderer 全链路，不含 React/SSE。

import {
  prepareChartV2,
  prepareChartV2All,
  type PrepareChartInputV2,
  type PrepareChartResultV2,
} from '../chartPipelineV2.js';
import { buildChartOption } from '../chartRegistry.js';
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

const MULTI_SERIES_TEMPORAL_DATA = {
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
// 6. boxplot gate 已翻转 → variant 可用
// ============================================================

test('boxplot gate flipped → prepareChartV2 allows boxplot variant', () => {
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
  assertOk(
    boxplot!.resolvedSuitability !== 'unsupported',
    `boxplot should no longer be unsupported, got: ${boxplot!.resolvedSuitability}`,
  );
  assertOk(boxplot!.spec !== null, 'boxplot spec should not be null');
  // 但 auto 场景下 detail_rows archetype 无 recommended 类型，defaultPlan 为 null
  // （boxplot 仅 allowed_explicit，bar 也仅 allowed_explicit）
});

test('boxplot: prepareChartV2All with grouped samples → boxplot success', () => {
  const input: PrepareChartInputV2 = {
    columns: ['station', 'ph_value'],
    rows: [
      { station: '站点A', ph_value: 7.2 },
      { station: '站点A', ph_value: 7.5 },
      { station: '站点A', ph_value: 6.8 },
      { station: '站点B', ph_value: 8.1 },
      { station: '站点B', ph_value: 7.9 },
      { station: '站点B', ph_value: 7.4 },
    ] as Row[],
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
    id: 'test-boxplot-1',
    title: 'Boxplot Test',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);
  assertOk(result.ok, `should succeed, got: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'boxplot');
  assertEqual(result.chart!.explicitType, true);

  // V2 transform 输出 spec 应含 yFields = ['min','q1','median','q3','max']
  assertOk(result.chart!.spec.yFields !== undefined, 'yFields should be defined');
  assertEqual(result.chart!.spec.yFields!.length, 5, 'yFields should have 5 stat fields');
  assertEqual(result.chart!.spec.yFields![0], 'min');

  // Renderer 验证
  const option = buildChartOption(result.chart!);
  assertOk(option !== null, 'buildChartOption should not return null');
  // 验证 series data 是五数概括数组
  const seriesData = (option as any).series?.[0]?.data;
  assertOk(Array.isArray(seriesData), 'series data should be array');
  assertEqual(seriesData.length, 2, 'should have 2 groups');
  assertEqual((seriesData[0] as number[]).length, 5, 'each box should have 5 values');
});

test('boxplot: user requested but data not eligible → fallback', () => {
  // 单值数据不适合 boxplot
  const input: PrepareChartInputV2 = {
    columns: ['total'],
    rows: [{ total: 100 }] as Row[],
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
    id: 'test-boxplot-fallback',
    title: 'Boxplot Fallback',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);
  // boxplot 应对此数据 unsupported → fallback
  assertOk(result.selectedPlan?.type !== 'boxplot' || !result.ok,
    'boxplot should not be selected for single-value data');
});

// ============================================================
// 7. multi-series line 仍被 gate 阻止
// ============================================================

test('multi-series line variant still blocked by renderer gate', () => {
  const input: PrepareChartInputV2 = {
    columns: MULTI_SERIES_TEMPORAL_DATA.columns,
    rows: MULTI_SERIES_TEMPORAL_DATA.rows,
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
// 9. 分类折线图：Planner 已审批，Renderer 不再否决
// ============================================================

test('categorical line: V2 Planner allows it, Pipeline must succeed', () => {
  // CATEGORICAL_DATA + user selects line → planner picks line_categorical_comparison
  // resolvedSuitability='allowed_explicit' → Planner 已明确允许
  // Pipeline 以 explicitType:true 调用 Renderer → Renderer 不再否决
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

  assertOk(result.selectedPlan !== null, 'should have selected plan');
  assertEqual(result.selectedPlan!.variantId, 'line_categorical_comparison');
  assertEqual(result.selectedPlan!.resolvedSuitability, 'allowed_explicit');
  assertOk(result.transformResult!.ok, 'transform should succeed');
  assertEqual(result.ok, true);
  assertEqual(result.errorCode, null);
  assertEqual(result.chart!.spec.type, 'line');
  assertOk(result.chart !== null, 'renderer should accept the plan');
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
// 13. 成功 ChartData 必须设置 explicitType: true
// ============================================================
//
// V2 Planner 已审批的 spec 进入 ChartView 后，应保留 V2 选择，
// 不被旧 pickDefault() 用 getChartTypeAvailability() 二次推荐覆盖。
// explicitType:true 使 ChartView pickDefault 分支1 生效（保留 chart.spec.type）。

test('successful ChartData has explicitType=true (preserve V2 selection)', () => {
  // 分类自动图表
  const catInput: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-13a',
    title: 'Test 13a',
    dataVersion: 1,
  };
  const catResult = prepareChartV2(catInput);
  assertOk(catResult.ok, `categorical should succeed, got: ${catResult.errorCode}`);
  assertEqual(catResult.chart!.explicitType, true, 'categorical chart.explicitType must be true');

  // 时间自动图表
  const timeInput: PrepareChartInputV2 = {
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-13b',
    title: 'Test 13b',
    dataVersion: 1,
  };
  const timeResult = prepareChartV2(timeInput);
  assertOk(timeResult.ok, `temporal should succeed, got: ${timeResult.errorCode}`);
  assertEqual(timeResult.chart!.explicitType, true, 'temporal chart.explicitType must be true');

  // 重复可加聚合图表（group_by_sum 后）
  const aggInput: PrepareChartInputV2 = {
    columns: DUPLICATE_ADDITIVE_DATA.columns,
    rows: DUPLICATE_ADDITIVE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-13c',
    title: 'Test 13c',
    dataVersion: 1,
  };
  const aggResult = prepareChartV2(aggInput);
  assertOk(aggResult.ok, `aggregated should succeed, got: ${aggResult.errorCode}`);
  assertEqual(aggResult.chart!.explicitType, true, 'aggregated chart.explicitType must be true');

  // 用户显式请求的分类折线图
  const lineInput: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'line',
    id: 'test-13d',
    title: 'Test 13d',
    dataVersion: 1,
  };
  const lineResult = prepareChartV2(lineInput);
  assertOk(lineResult.ok, `categorical line should succeed, got: ${lineResult.errorCode}`);
  assertEqual(lineResult.chart!.explicitType, true, 'categorical line chart.explicitType must be true');
});

// ============================================================
// 防御分支说明
// ============================================================
//
// 以下错误码在当前 pilot 无法通过公开输入真实触发，但保留在生产代码中：
//
//   selected_plan_missing_spec
//     → 仅在 defaultPlan.spec===null 时触发。当前 Planner 在 spec===null
//       时 resolvedSuitability 为 'unsupported'，不会被选为 defaultPlan。
//       待后续支持"无 spec 但有语义标记"的 capability 时可测试。
//
//   transform_failed_<code>
//     → 当前 pilot transform（none/group_by_sum）对于 Planner 输出的 spec
//       不会失败：none 始终成功，group_by_sum 的输入已通过 trait 校验。
//       待后续引入可能失败的 transform（如 boxplot_summary 样本不足）时可测试。
//
//   renderer_rejected_plan
//     → Pipeline 以 explicitType:true 调用 Renderer，跳过旧推荐逻辑的二次判断。
//       当前 pilot 的 bar/line 输出结构均与 buildAxisChart 兼容。
//       待后续引入新 renderer（如预计算 boxplot）或 schema 不兼容的输出时可测试。
//
// 以上分支不得通过类型断言、伪造 Planner 结果或修改生产接口制造覆盖。

// ============================================================
// B-5B: prepareChartV2All 使用 ALL_CAPABILITIES_V2
// ============================================================

// 单 KPI 数据（PILOT 无 gauge → ALL 有 gauge）
const SINGLE_KPI_DATA = {
  columns: ['total_count'] as string[],
  rows: [{ total_count: 342 }] as Row[],
};

// 两数值关系数据（PILOT 无 scatter → ALL 有 scatter）
const TWO_NUMERIC_DATA = {
  columns: ['rainfall', 'runoff'] as string[],
  rows: [
    { rainfall: 12.5, runoff: 3.2 },
    { rainfall: 25.0, runoff: 7.8 },
    { rainfall: 8.0, runoff: 1.9 },
    { rainfall: 30.5, runoff: 10.1 },
    { rainfall: 15.0, runoff: 4.5 },
    { rainfall: 22.0, runoff: 6.7 },
  ] as Row[],
};

// 三数值关系数据（PILOT 无 bubble → ALL 有 bubble）
const THREE_NUMERIC_DATA = {
  columns: ['rainfall', 'runoff', 'area'] as string[],
  rows: [
    { rainfall: 12.5, runoff: 3.2, area: 150 },
    { rainfall: 25.0, runoff: 7.8, area: 300 },
    { rainfall: 8.0, runoff: 1.9, area: 100 },
    { rainfall: 30.5, runoff: 10.1, area: 350 },
    { rainfall: 15.0, runoff: 4.5, area: 180 },
    { rainfall: 22.0, runoff: 6.7, area: 250 },
  ] as Row[],
};

test('B-5B: prepareChartV2All single_kpi → gauge (ALL 新能力)', () => {
  const input: PrepareChartInputV2 = {
    columns: SINGLE_KPI_DATA.columns,
    rows: SINGLE_KPI_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5b-1',
    title: 'B-5B Gauge',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'gauge');
  assertEqual(result.selectedPlan!.variantId, 'gauge_single_value');
  assertEqual(result.errorCode, null);
});

test('B-5B: prepareChartV2All two_numeric → scatter (ALL 新能力)', () => {
  const input: PrepareChartInputV2 = {
    columns: TWO_NUMERIC_DATA.columns,
    rows: TWO_NUMERIC_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5b-2',
    title: 'B-5B Scatter',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'scatter');
  assertEqual(result.selectedPlan!.variantId, 'scatter_numeric_relationship');
  assertEqual(result.errorCode, null);
});

test('B-5B: prepareChartV2All three_numeric → bubble (ALL 新能力)', () => {
  const input: PrepareChartInputV2 = {
    columns: THREE_NUMERIC_DATA.columns,
    rows: THREE_NUMERIC_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5b-3',
    title: 'B-5B Bubble',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'bubble');
  assertEqual(result.selectedPlan!.variantId, 'bubble_numeric_relationship');
  assertEqual(result.errorCode, null);
});

test('B-5B: prepareChartV2All 分类数据仍可用 bar', () => {
  // ALL 中 bar 与 PILOT 行为一致
  const input: PrepareChartInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5b-4',
    title: 'B-5B Bar',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'bar');
  assertEqual(result.selectedPlan!.variantId, 'bar_categorical_comparison');
});

test('B-5B: prepareChartV2All 不改变 explicitType=true', () => {
  const input: PrepareChartInputV2 = {
    columns: TWO_NUMERIC_DATA.columns,
    rows: TWO_NUMERIC_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5b-5',
    title: 'B-5B ExplicitType',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok);
  assertEqual(result.chart!.explicitType, true,
    'prepareChartV2All must set explicitType=true (same as prepareChartV2)');
});

// ============================================================
// B-5C: prepareChartV2All 覆盖关键运行时数据形态
// ============================================================

const REGION_COUNT_DATA = {
  columns: ['region', 'count'] as string[],
  rows: [
    { region: '城北区', count: 45 },
    { region: '城南区', count: 32 },
    { region: '城东区', count: 28 },
    { region: '城西区', count: 19 },
    { region: '中心区', count: 56 },
  ] as Row[],
};

const MONTH_DISCHARGE_DATA = {
  columns: ['month', 'discharge'] as string[],
  rows: [
    { month: '1月', discharge: 120 },
    { month: '2月', discharge: 115 },
    { month: '3月', discharge: 130 },
    { month: '4月', discharge: 125 },
    { month: '5月', discharge: 140 },
    { month: '6月', discharge: 135 },
  ] as Row[],
};

const MONITORING_DETAIL_DATA = {
  columns: ['sample_id', 'sampling_point', 'ph_value', 'cod_value', 'nh3n_value'] as string[],
  rows: [
    { sample_id: 1, sampling_point: 'SP-A', ph_value: 7.2, cod_value: 12.0, nh3n_value: 0.5 },
    { sample_id: 2, sampling_point: 'SP-B', ph_value: 7.8, cod_value: 18.0, nh3n_value: 0.8 },
    { sample_id: 3, sampling_point: 'SP-A', ph_value: 7.5, cod_value: 10.0, nh3n_value: 0.4 },
    { sample_id: 4, sampling_point: 'SP-C', ph_value: 7.0, cod_value: 8.0, nh3n_value: 0.3 },
    { sample_id: 5, sampling_point: 'SP-B', ph_value: 8.1, cod_value: 20.0, nh3n_value: 0.9 },
    { sample_id: 6, sampling_point: 'SP-A', ph_value: 6.8, cod_value: 15.0, nh3n_value: 0.6 },
  ] as Row[],
};

test('B-5C: region_count → bar (V2 auto 默认柱状图)', () => {
  const input: PrepareChartInputV2 = {
    columns: REGION_COUNT_DATA.columns,
    rows: REGION_COUNT_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-region',
    title: 'Region Count',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'bar');
  assertEqual(result.selectedPlan!.variantId, 'bar_categorical_comparison');
  assertEqual(result.planning.profile.archetype, 'categorical_series');
});

test('B-5C: month_discharge → line (V2 auto 默认折线图)', () => {
  const input: PrepareChartInputV2 = {
    columns: MONTH_DISCHARGE_DATA.columns,
    rows: MONTH_DISCHARGE_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-month',
    title: 'Month Discharge',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertOk(result.ok, `should succeed, got errorCode: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'line');
  assertEqual(result.selectedPlan!.variantId, 'line_temporal_trend_single');
  assertEqual(result.planning.profile.archetype, 'temporal_series');
});

test('B-5C: monitoring_detail → no_default_plan (detail_rows 不自动出图)', () => {
  const input: PrepareChartInputV2 = {
    columns: MONITORING_DETAIL_DATA.columns,
    rows: MONITORING_DETAIL_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-detail',
    title: 'Monitoring Detail',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertEqual(result.ok, false, 'detail_rows should not produce auto chart');
  assertEqual(result.errorCode, 'no_default_plan');
  assertEqual(result.chart, null);
  assertEqual(result.planning.profile.archetype, 'detail_rows');
  // P1 修复验证：有 identifierFields(id) + 多 measure → detail_rows
  assertOk(result.planning.profile.identifierFields.length > 0,
    'monitoring detail should have identifier fields');
});

test('B-5C: repeated_region_count → no_default_plan (P3 修复)', () => {
  const repeatedData = {
    columns: ['region', 'count'] as string[],
    rows: [
      { region: '城北', count: 10 },
      { region: '城北', count: 15 },
      { region: '城南', count: 8 },
      { region: '城南', count: 12 },
    ] as Row[],
  };

  const input: PrepareChartInputV2 = {
    columns: repeatedData.columns,
    rows: repeatedData.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-repeated',
    title: 'Repeated Region',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertEqual(result.ok, false, 'repeated_region_count should not produce auto chart');
  assertEqual(result.errorCode, 'no_default_plan');
  assertEqual(result.planning.profile.archetype, 'detail_rows');
});

test('B-5C: heterogeneous_metric_rows → no_default_plan', () => {
  const heteroData = {
    columns: ['metric_name', 'value'] as string[],
    rows: [
      { metric_name: 'BOD监测记录总数', value: 16 },
      { metric_name: '涉及排污口数量', value: 16 },
      { metric_name: '平均每个排污口记录数', value: 1 },
    ] as Row[],
  };

  const input: PrepareChartInputV2 = {
    columns: heteroData.columns,
    rows: heteroData.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-hetero',
    title: 'Hetero',
    dataVersion: 1,
  };

  const result = prepareChartV2All(input);

  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_default_plan');
  assertEqual(result.planning.profile.archetype, 'heterogeneous_metric_rows');
});

test('B-5C: prepareChartV2All output has expected ChartData structure', () => {
  // 验证成功输出的 ChartData 结构完整
  const input: PrepareChartInputV2 = {
    columns: REGION_COUNT_DATA.columns,
    rows: REGION_COUNT_DATA.rows,
    source: 'auto',
    intent: 'auto',
    id: 'test-b5c-struct',
    title: 'Structure Check',
    dataVersion: 42,
  };

  const result = prepareChartV2All(input);
  assertOk(result.ok);

  const c = result.chart!;
  assertEqual(c.id, 'test-b5c-struct');
  assertEqual(c.title, 'Structure Check');
  assertEqual(c.dataVersion, 42);
  assertEqual(c.explicitType, true);
  // columns/rows/spec 来自 transformResult（不混用原始输入）
  assertOk(c.columns === result.transformResult!.columns);
  assertOk(c.rows === result.transformResult!.rows);
  assertOk(c.spec === result.transformResult!.spec);
  // spec.type 必须为合法的 RenderableChartType
  assertOk(
    ['bar', 'line', 'scatter', 'bubble', 'gauge', 'pie', 'donut',
     'area', 'horizontal_bar', 'radar', 'heatmap', 'boxplot', 'combo'].includes(c.spec.type),
    `unexpected chart type: ${c.spec.type}`,
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
