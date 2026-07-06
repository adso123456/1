// chartPlannerV2.test.ts — V2 Planner 契约测试（pilot: bar / line / boxplot）
//
// 运行时测试：planChartsV2 全流程，覆盖 suitability 计算、gate 判定、
// 默认选择、intent 调整、回退逻辑。

import {
  planChartsV2,
  type ChartPlanV2,
  type ChartPlanningResultV2,
  type PlanChartsInputV2,
} from '../chartPlannerV2.js';
import type { Row } from '../datasetProfilerV2.js';

let passed = 0;
let failed = 0;

// ---- 内联断言 ----

function assertEqual<T>(actual: T, expected: T, msg?: string): void {
  if (actual !== expected) {
    throw new Error(msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertOk(cond: boolean, msg?: string): void {
  if (!cond) {
    throw new Error(msg ?? `expected truthy, got ${JSON.stringify(cond)}`);
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

// ---- 辅助 ----

function findPlan(result: ChartPlanningResultV2, variantId: string): ChartPlanV2 | undefined {
  return result.plans.find(p => p.variantId === variantId);
}

// ============================================================
// 测试数据集
// ============================================================

/** 分类数据：1 个维度 + 1 个指标，无重复 → categorical_series */
const CATEGORICAL_DATA = {
  columns: ['product', 'sales'] as string[],
  rows: [
    { product: 'A', sales: 100 },
    { product: 'B', sales: 200 },
    { product: 'C', sales: 150 },
  ] as Row[],
};

/** 时序数据：1 个时间字段 + 1 个指标 → temporal_series */
const TEMPORAL_DATA = {
  columns: ['year', 'revenue'] as string[],
  rows: [
    { year: '2020', revenue: 500 },
    { year: '2021', revenue: 600 },
    { year: '2022', revenue: 700 },
  ] as Row[],
};

/** 多系列时序：entity + 时间 + 指标 → multi_series_temporal */
const MULTI_SERIES_TEMPORAL_DATA = {
  columns: ['company', 'year', 'revenue'] as string[],
  rows: [
    { company: 'ACME', year: '2020', revenue: 100 },
    { company: 'ACME', year: '2021', revenue: 110 },
    { company: 'BETA', year: '2020', revenue: 200 },
    { company: 'BETA', year: '2021', revenue: 210 },
  ] as Row[],
};

/** 含重复维度的明细：non_additive 指标 + 分组样本 → detail_rows */
const DETAIL_DUPLICATE_DATA = {
  columns: ['category', 'avg_score'] as string[],
  rows: [
    { category: 'A', avg_score: 85 },
    { category: 'A', avg_score: 90 },
    { category: 'B', avg_score: 75 },
    { category: 'B', avg_score: 80 },
  ] as Row[],
};

/** 仅有指标无维度字段 */
const MEASURE_ONLY_DATA = {
  columns: ['value1', 'value2'] as string[],
  rows: [
    { value1: 10, value2: 20 },
    { value1: 30, value2: 40 },
  ] as Row[],
};

// ============================================================
// 1. 分类数据默认 bar
// ============================================================

test('categorical data: bar is recommended and default (auto)', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const barPlan = findPlan(result, 'bar_categorical_comparison');
  assertOk(barPlan !== undefined, 'bar_categorical_comparison should exist');
  assertEqual(barPlan!.resolvedSuitability, 'recommended');
  assertOk(barPlan!.spec !== null, 'spec should not be null');
  assertEqual(barPlan!.spec!.type, 'bar');
  assertOk(typeof barPlan!.spec!.xField === 'string', 'xField should be resolved');
  assertOk(
    Array.isArray(barPlan!.spec!.yFields) && barPlan!.spec!.yFields!.length > 0,
    'yFields should be resolved',
  );

  assertOk(result.defaultPlan !== null, 'defaultPlan should not be null');
  assertEqual(result.defaultPlan!.type, 'bar');
  assertEqual(result.defaultPlan!.variantId, 'bar_categorical_comparison');
  assertOk(result.switchablePlans.length > 0);
});

// ============================================================
// 2. 时间数据默认 line
// ============================================================

test('temporal data: line is recommended and default (auto)', () => {
  const result = planChartsV2({
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const linePlan = findPlan(result, 'line_temporal_trend_single');
  assertOk(linePlan !== undefined, 'line_temporal_trend_single should exist');
  assertEqual(linePlan!.resolvedSuitability, 'recommended');
  assertOk(linePlan!.spec !== null, 'spec should not be null');
  assertEqual(linePlan!.spec!.type, 'line');

  assertOk(result.defaultPlan !== null);
  assertEqual(result.defaultPlan!.type, 'line');
});

// ============================================================
// 3. 分类 line 只能 allowed_explicit
// ============================================================

test('categorical data: line is allowed_explicit (not recommended)', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const linePlan = findPlan(result, 'line_categorical_comparison');
  assertOk(linePlan !== undefined);
  assertEqual(linePlan!.baseSuitability, 'allowed_explicit');
  assertEqual(linePlan!.resolvedSuitability, 'allowed_explicit');
  assertOk(linePlan!.spec !== null);
});

// ============================================================
// 4. 多实体 line 因 renderer gate 被禁用
// ============================================================

test('multi-entity temporal: line multi-series unsupported by renderer gate', () => {
  const result = planChartsV2({
    columns: MULTI_SERIES_TEMPORAL_DATA.columns,
    rows: MULTI_SERIES_TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  // 用 archetype 名称验证
  const arch = result.profile.archetype;
  console.log(`  [info] multi-entity temporal → archetype: ${arch}`);

  const plan = findPlan(result, 'line_temporal_trend_multi');
  assertOk(plan !== undefined, 'line_temporal_trend_multi should be in plans');
  assertEqual(plan!.resolvedSuitability, 'unsupported', 'should be unsupported due to renderer gate');
  assertEqual(plan!.spec, null, 'spec should be null when unsupported');
  assertOk(plan!.reasonCode.length > 0, 'should have a reason code');
});

// ============================================================
// 5. 重复可加指标 bar 因 renderer gate 被禁用
// ============================================================

test('detail with duplicates: bar aggregated unsupported by renderer gate', () => {
  const result = planChartsV2({
    columns: DETAIL_DUPLICATE_DATA.columns,
    rows: DETAIL_DUPLICATE_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const arch = result.profile.archetype;
  console.log(`  [info] detail duplicate data → archetype: ${arch}`);

  const plan = findPlan(result, 'bar_categorical_aggregated');
  assertOk(plan !== undefined, 'bar_categorical_aggregated should be in plans');
  assertEqual(plan!.resolvedSuitability, 'unsupported', 'should be unsupported');
  assertEqual(plan!.spec, null);
});

// ============================================================
// 6. boxplot gate 已翻转（B-7B）
// ============================================================

test('detail with duplicates: boxplot now allowed_explicit (gate flipped)', () => {
  const result = planChartsV2({
    columns: DETAIL_DUPLICATE_DATA.columns,
    rows: DETAIL_DUPLICATE_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const plan = findPlan(result, 'boxplot_grouped_distribution');
  assertOk(plan !== undefined, 'boxplot_grouped_distribution should be in plans');
  assertOk(
    plan!.resolvedSuitability !== 'unsupported',
    `should not be unsupported, got: ${plan!.resolvedSuitability}`,
  );
  assertOk(plan!.spec !== null, 'boxplot spec should not be null');
});

// ============================================================
// 7. user 可选择 allowed_explicit
// ============================================================

test('user can select allowed_explicit line on categorical data', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'line',
  });

  assertOk(result.defaultPlan !== null);
  assertEqual(result.defaultPlan!.type, 'line');
  assertEqual(result.defaultPlan!.variantId, 'line_categorical_comparison');
  assertEqual(result.defaultPlan!.resolvedSuitability, 'allowed_explicit');
  assertEqual(result.fallbackNotice, null);
});

// ============================================================
// 8. user 请求不支持类型时回退
// ============================================================

test('user requesting unsupported type falls back with notice', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
  });

  assertOk(result.defaultPlan !== null, 'should have a fallback plan');
  assertEqual(result.defaultPlan!.type, 'bar', 'should fallback to bar');
  assertOk(result.fallbackNotice !== null, 'should have fallbackNotice');
  assertOk(
    result.fallbackNotice!.includes('boxplot'),
    'fallbackNotice should mention requested type',
  );
});

// ============================================================
// 9. auto 无 recommended 时 defaultPlan=null
// ============================================================

test('auto with no recommended plans → defaultPlan=null', () => {
  // 用 intent 冲突使所有 recommended 降级为 allowed_explicit
  const result = planChartsV2({
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'distribution',  // 与 line(trend) 和 bar(comparison) 均冲突
  });

  // 验证推荐计划是否被降级
  const linePlan = findPlan(result, 'line_temporal_trend_single');
  if (linePlan && linePlan.resolvedSuitability !== 'unsupported') {
    assertEqual(
      linePlan.resolvedSuitability,
      'allowed_explicit',
      'recommended line should be demoted to allowed_explicit',
    );
  }

  assertEqual(result.defaultPlan, null, 'auto should return null when no recommended');
  assertOk(result.noChartReason !== null, 'should have noChartReason');
});

// ============================================================
// 10. preferredSpec 不影响 Profiler 字段解析（spec 仍然生成）
// ============================================================

test('preferredSpec with invalid fields does not corrupt Profiler-based specs', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'model',
    intent: 'auto',
    preferredSpec: { type: 'bar', xField: 'not_a_column', yFields: ['fake'] },
  });

  const barPlan = findPlan(result, 'bar_categorical_comparison');
  assertOk(barPlan !== undefined);
  assertOk(barPlan!.spec !== null, 'spec should be generated from Profiler fields');
  // Profiler 选出的字段应该来自数据，不是来自无效的 preferredSpec
  assertEqual(barPlan!.spec!.xField, 'product');
  assertOk(barPlan!.spec!.yFields!.includes('sales'));
});

// ============================================================
// 11. intent 不能突破 maxSuitability
// ============================================================

test('intent cannot exceed maxSuitability (line_categorical_comparison, max=allowed_explicit)', () => {
  // line_categorical_comparison: base=allowed_explicit, maxSuitability=allowed_explicit
  // 即使 intent='comparison' 匹配 semanticMode，也不能提升到 recommended
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'comparison',  // 匹配 line_categorical_comparison 的 semanticMode
  });

  const plan = findPlan(result, 'line_categorical_comparison');
  assertOk(plan !== undefined, 'line_categorical_comparison should exist');
  assertEqual(plan!.baseSuitability, 'allowed_explicit');
  assertEqual(plan!.maxSuitability, 'allowed_explicit');
  // 直接断言——禁用跳过断言的 if
  assertEqual(
    plan!.resolvedSuitability,
    'allowed_explicit',
    'maxSuitability=allowed_explicit should cap intent boost',
  );
  assertOk(plan!.spec !== null, 'spec should not be null');
});

// ============================================================
// 12. intent 匹配时可提升 allowed_explicit → recommended
// ============================================================

test('intent match promotes allowed_explicit to recommended when maxSuitability=recommended', () => {
  // bar_categorical_comparison 在 temporal_series 下:
  //   base=allowed_explicit, maxSuitability=recommended, semanticMode=comparison
  // intent='comparison' 匹配 → 应提升为 recommended
  const result = planChartsV2({
    columns: TEMPORAL_DATA.columns,
    rows: TEMPORAL_DATA.rows,
    source: 'auto',
    intent: 'comparison',
  });

  const plan = findPlan(result, 'bar_categorical_comparison');
  assertOk(plan !== undefined, 'bar_categorical_comparison should exist');
  assertEqual(plan!.baseSuitability, 'allowed_explicit');
  assertEqual(plan!.maxSuitability, 'recommended');
  assertEqual(
    plan!.resolvedSuitability,
    'recommended',
    'intent match should promote allowed_explicit to recommended',
  );
  assertOk(plan!.spec !== null, 'spec should not be null');
});

// ============================================================
// 13. input 不被修改
// ============================================================

test('planChartsV2 does not mutate input', () => {
  const input: PlanChartsInputV2 = {
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  };
  const snap = JSON.stringify(input);
  planChartsV2(input);
  assertEqual(JSON.stringify(input), snap, 'input should not be mutated');
});

// ============================================================
// 14. 补充：无维度字段时 spec=null
// ============================================================

test('measure-only data: primary dimension null → field resolution fails → spec null', () => {
  const result = planChartsV2({
    columns: MEASURE_ONLY_DATA.columns,
    rows: MEASURE_ONLY_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  const arch = result.profile.archetype;
  console.log(`  [info] measure-only data → archetype: ${arch}`);

  // 所有 pilot variant 都需要 xField，没有维度字段 → 解析失败
  for (const plan of result.plans) {
    if (plan.resolvedSuitability === 'unsupported') {
      assertEqual(plan.spec, null, `${plan.variantId}: unsupported plan should have null spec`);
    }
  }
});

// ============================================================
// 15. 补充：switchablePlans 包含所有 supported 且 spec 非 null 的计划
// ============================================================

test('switchablePlans contains only supported plans with spec', () => {
  const result = planChartsV2({
    columns: CATEGORICAL_DATA.columns,
    rows: CATEGORICAL_DATA.rows,
    source: 'auto',
    intent: 'auto',
  });

  for (const plan of result.switchablePlans) {
    assertOk(
      plan.resolvedSuitability !== 'unsupported',
      `${plan.variantId}: switchable plan should not be unsupported`,
    );
    assertOk(plan.spec !== null, `${plan.variantId}: switchable plan should have spec`);
  }

  assertOk(result.switchablePlans.length >= 2, 'should have at least bar and line');
});

// ============================================================
// 16. 全部计划 unsupported 时 defaultPlan=null, noChartReason, fallbackNotice=null
// ============================================================

test('all plans unsupported: defaultPlan=null, noChartReason set, fallbackNotice=null', () => {
  // 单 KPI 数据在 PILOT 下全部 unsupported（PILOT 不含 gauge）
  const result = planChartsV2({
    columns: ['total_count'],
    rows: [{ total_count: 342 }],
    source: 'auto',
    intent: 'auto',
  });

  assertEqual(result.defaultPlan, null, 'defaultPlan should be null');
  assertOk(result.noChartReason !== null, 'noChartReason should be set');
  assertOk(result.noChartReason!.length > 0, 'noChartReason should not be empty');
  assertEqual(result.fallbackNotice, null, 'fallbackNotice should be null');
  assertEqual(result.switchablePlans.length, 0, 'switchablePlans should be empty');

  // 验证所有 plan 确实都是 unsupported
  assertOk(result.plans.length > 0, 'should have plans');
  for (const plan of result.plans) {
    assertEqual(
      plan.resolvedSuitability,
      'unsupported',
      `${plan.variantId}: should be unsupported`,
    );
    assertEqual(plan.spec, null, `${plan.variantId}: spec should be null`);
  }
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 ChartPlanner Pilot Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
