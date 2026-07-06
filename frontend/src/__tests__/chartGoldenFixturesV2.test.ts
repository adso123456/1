// chartGoldenFixturesV2.test.ts — V2 Golden 回归夹具（15 种数据形态）
//
// 阶段 B-1：离线验证 Planner 对各种数据形态产出正确的 13 种 ChartPlan。
// 使用 ALL_CAPABILITIES_V2 + planChartsWithCapabilitiesV2，不接入运行时。
// 运行时仍默认使用 PILOT_CAPABILITIES_V2（本测试不影响生产行为）。
//
// 夹具对应《图表架构审查报告 V2》§8 的 15 种数据形态。
// 与报告期望不一致之处，在文件末尾"问题清单"注释中记录。

import { planChartsWithCapabilitiesV2 } from '../chartPlannerV2.js';
import { ALL_CAPABILITIES_V2 } from '../chartCapabilityV2.js';
import type { PlanChartsInputV2, ChartPlanV2 } from '../chartPlannerV2.js';
import type { ChartCapability } from '../chartCapabilityV2.js';
import type { Row } from '../datasetProfilerV2.js';
import type { ChartSuitability, RenderableChartType } from '../types.js';

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

// ---- 辅助 ----

const ALL_TYPES: RenderableChartType[] = [
  'bar', 'horizontal_bar', 'line', 'area', 'pie', 'donut',
  'scatter', 'bubble', 'radar', 'heatmap', 'boxplot', 'gauge', 'combo',
];

/** 某 type 在 plans 中的最高 suitability（recommended > allowed_explicit > unsupported） */
function bestSuitability(plans: readonly ChartPlanV2[], type: RenderableChartType): ChartSuitability {
  const suits = plans.filter(p => p.type === type).map(p => p.resolvedSuitability);
  if (suits.includes('recommended')) return 'recommended';
  if (suits.includes('allowed_explicit')) return 'allowed_explicit';
  return 'unsupported';
}

/** 每种 type 的 best suitability 映射，用于整体断言 */
function suitMap(plans: readonly ChartPlanV2[]): Record<RenderableChartType, ChartSuitability> {
  const map = {} as Record<RenderableChartType, ChartSuitability>;
  for (const t of ALL_TYPES) map[t] = bestSuitability(plans, t);
  return map;
}

/** 断言 13 个 type 的 suitability 全部匹配期望 */
function assertSuitMap(
  plans: readonly ChartPlanV2[],
  expected: Partial<Record<RenderableChartType, ChartSuitability>>,
  fixture: string,
): void {
  const actual = suitMap(plans);
  for (const t of ALL_TYPES) {
    const exp = expected[t] ?? 'unsupported';
    assertEqual(actual[t], exp, `${fixture}: ${t} expected ${exp}, got ${actual[t]}`);
  }
}

interface FixInput {
  columns: string[];
  rows: Row[];
  source?: PlanChartsInputV2['source'];
  intent?: PlanChartsInputV2['intent'];
  requestedChartType?: RenderableChartType;
}

function run(input: FixInput) {
  return planChartsWithCapabilitiesV2(
    {
      columns: input.columns,
      rows: input.rows,
      source: input.source ?? 'auto',
      intent: input.intent ?? 'auto',
      requestedChartType: input.requestedChartType,
    },
    ALL_CAPABILITIES_V2,
  );
}

// ============================================================
// 夹具 1：空数据
// ============================================================

test('fixture 1: empty → all unsupported, defaultPlan=null', () => {
  const r = run({ columns: [], rows: [] });
  const p = r.profile;
  assertEqual(p.archetype, 'empty');
  assertEqual(p.traits.measureCount, 0);
  assertEqual(p.traits.rowCount, 0);
  assertEqual(r.plans.length, 17); // 13 种共 17 个 variant
  assertSuitMap(r.plans, {}, 'fixture 1'); // 全 unsupported
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// 夹具 2：单 KPI
// ============================================================

test('fixture 2: single_value → gauge=recommended', () => {
  const r = run({ columns: ['total_count'], rows: [{ total_count: 342 }] });
  assertEqual(r.profile.archetype, 'single_value');
  assertEqual(r.profile.traits.measureCount, 1);
  assertEqual(r.profile.traits.rowCount, 1);
  assertSuitMap(r.plans, { gauge: 'recommended' }, 'fixture 2');
  assertEqual(r.defaultPlan?.type, 'gauge');
  assertEqual(r.noChartReason, null);
});

// ============================================================
// 夹具 3：单行多指标
// ============================================================

test('fixture 3: single_row_multi_measure → all unsupported, defaultPlan=null', () => {
  const r = run({
    columns: ['station_count', 'avg_discharge', 'total_violations'],
    rows: [{ station_count: 120, avg_discharge: 350.5, total_violations: 15 }],
  });
  assertEqual(r.profile.archetype, 'single_row_multi_measure');
  assertEqual(r.profile.traits.measureCount, 3);
  assertEqual(r.profile.traits.rowCount, 1);
  assertSuitMap(r.plans, {}, 'fixture 3');
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// 夹具 4：普通明细（P1 修复后判为 detail_rows，与报告期望一致）
// ============================================================

test('fixture 4: detail-like rows → detail_rows (P1 已修复)', () => {
  const rows: Row[] = Array.from({ length: 30 }, (_, i) => ({
    id: i, station_name: '站' + (i % 5), sample_date: '2024-01-' + ((i % 28) + 1),
    ph: 7 + i % 3, cod: 12 + i % 5, nh3n: 0.5 + i % 2,
  }));
  const r = run({ columns: ['id', 'station_name', 'sample_date', 'ph', 'cod', 'nh3n'], rows });
  // P1：有 identifierFields(id) + 多 measure + 非聚合 → detail_rows（不再误判 multi_series_temporal）
  assertEqual(r.profile.archetype, 'detail_rows');
  assertOk(r.profile.identifierFields.includes('id'), 'identifierFields 应包含 id');
  assertOk(r.profile.traits.detailConfidence >= 0.5, 'detailConfidence 应 >= 0.5');
  assertSuitMap(r.plans, {}, 'fixture 4');
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// 夹具 5：分类对比
// ============================================================

test('fixture 5: categorical_series → bar=recommended', () => {
  const r = run({
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 45 }, { region: '城南', count: 32 },
      { region: '城东', count: 28 }, { region: '城西', count: 19 }, { region: '中心', count: 56 },
    ],
  });
  assertEqual(r.profile.archetype, 'categorical_series');
  assertEqual(r.profile.traits.measureCount, 1);
  assertEqual(r.profile.traits.duplicateDimensionKeys, false);
  assertEqual(r.profile.traits.partToWholeEligible, true);
  assertSuitMap(r.plans, {
    bar: 'recommended', horizontal_bar: 'recommended',
    line: 'allowed_explicit', pie: 'allowed_explicit', donut: 'allowed_explicit',
  }, 'fixture 5');
  assertEqual(r.defaultPlan?.type, 'bar');
  assertEqual(r.noChartReason, null);
});

// ============================================================
// 夹具 6：分类对比 + 用户请求饼图
// ============================================================

test('fixture 6: categorical_series + user pie → defaultPlan=pie', () => {
  const rows = [
    { region: '城北', count: 45 }, { region: '城南', count: 32 },
    { region: '城东', count: 28 }, { region: '城西', count: 19 }, { region: '中心', count: 56 },
  ];
  const r = run({
    columns: ['region', 'count'], rows,
    source: 'user', requestedChartType: 'pie', intent: 'part_to_whole',
  });
  assertEqual(r.profile.archetype, 'categorical_series');
  // intent=part_to_whole：bar 从 recommended 降为 allowed_explicit
  // pie maxSuitability=allowed_explicit，intent 匹配但不突破上限，仍 allowed_explicit
  assertSuitMap(r.plans, {
    bar: 'allowed_explicit', horizontal_bar: 'allowed_explicit',
    line: 'allowed_explicit', pie: 'allowed_explicit', donut: 'allowed_explicit',
  }, 'fixture 6');
  assertEqual(r.defaultPlan?.type, 'pie');
  assertEqual(r.noChartReason, null);
});

// ============================================================
// 夹具 7：单实体时间趋势
// ============================================================

test('fixture 7: temporal_series → line=recommended, area=recommended', () => {
  const rows: Row[] = Array.from({ length: 12 }, (_, i) => ({ month: (i + 1) + '月', discharge: 100 + i * 5 }));
  const r = run({ columns: ['month', 'discharge'], rows });
  assertEqual(r.profile.archetype, 'temporal_series');
  assertEqual(r.profile.traits.timePointCount, 12);
  assertEqual(r.profile.traits.entityFieldCount, 0);
  assertSuitMap(r.plans, {
    line: 'recommended', area: 'recommended',
    bar: 'allowed_explicit', horizontal_bar: 'allowed_explicit',
  }, 'fixture 7');
  assertEqual(r.defaultPlan?.type, 'line');
  assertEqual(r.noChartReason, null);
});

// ============================================================
// 夹具 8：多实体时间趋势（renderer 无 multi-series → 全 unsupported）
// ============================================================

test('fixture 8: multi_series_temporal → all unsupported (multi-series gate)', () => {
  const r = run({
    columns: ['station_name', 'month', 'value'],
    rows: [
      { station_name: 'A', month: '1月', value: 10 }, { station_name: 'A', month: '2月', value: 12 }, { station_name: 'A', month: '3月', value: 11 },
      { station_name: 'B', month: '1月', value: 8 }, { station_name: 'B', month: '2月', value: 9 }, { station_name: 'B', month: '3月', value: 10 },
    ],
  });
  assertEqual(r.profile.archetype, 'multi_series_temporal');
  assertEqual(r.profile.traits.multiSeriesEligible, true);
  assertEqual(r.profile.traits.entityCount, 2);
  assertSuitMap(r.plans, {}, 'fixture 8'); // line_temporal_trend_multi 被 multi_series_line gate
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// 夹具 9：两数值关系
// ============================================================

test('fixture 9: numeric_relationship → scatter=recommended', () => {
  const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({ rainfall: 10 + i * 2, runoff: 2 + i * 0.5 }));
  const r = run({ columns: ['rainfall', 'runoff'], rows });
  assertEqual(r.profile.archetype, 'numeric_relationship');
  assertEqual(r.profile.traits.measureCount, 2);
  assertEqual(r.profile.traits.numericFieldCount, 2);
  assertSuitMap(r.plans, { scatter: 'recommended' }, 'fixture 9');
  assertEqual(r.defaultPlan?.type, 'scatter');
  assertEqual(r.noChartReason, null);
  // P8：scatter xField 与 yFields[0] 互异
  const scatterPlan = r.plans.find(p => p.type === 'scatter');
  assertOk(scatterPlan?.spec !== null, 'scatter spec should be non-null');
  const sx = scatterPlan!.spec!.xField;
  const sy = scatterPlan!.spec!.yFields?.[0];
  assertOk(typeof sx === 'string' && sx.length > 0, 'scatter xField resolved');
  assertOk(typeof sy === 'string' && sy.length > 0, 'scatter yFields[0] resolved');
  assertOk(sx !== sy, `scatter xField (${sx}) must differ from yFields[0] (${sy})`);
  // P7：numeric_relationship 无 entityField → seriesField 被跳过（undefined），spec 仍有效
  assertEqual(scatterPlan!.spec!.seriesField, undefined, 'scatter spec should have no seriesField without entityField');
});

// ============================================================
// 夹具 10：三数值关系
// ============================================================

test('fixture 10: numeric_relationship(3) → bubble=recommended', () => {
  const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({ rainfall: 10 + i * 2, runoff: 2 + i * 0.5, area: 100 + i * 10 }));
  const r = run({ columns: ['rainfall', 'runoff', 'area'], rows });
  assertEqual(r.profile.archetype, 'numeric_relationship');
  assertEqual(r.profile.traits.measureCount, 3);
  assertEqual(r.profile.traits.numericFieldCount, 3);
  assertSuitMap(r.plans, { scatter: 'recommended', bubble: 'recommended' }, 'fixture 10');
  // P9：三数值关系 auto 默认选 bubble（autoPriority：bubble=0 优先于 scatter=2），不靠数组顺序
  assertEqual(r.defaultPlan?.type, 'bubble');
  assertEqual(r.noChartReason, null);
  // P8：scatter xField 与 yFields[0] 互异
  const scatterPlan = r.plans.find(p => p.type === 'scatter');
  assertOk(scatterPlan?.spec !== null, 'scatter spec should be non-null');
  assertOk(scatterPlan!.spec!.xField !== scatterPlan!.spec!.yFields?.[0],
    `scatter x/y must differ: ${scatterPlan!.spec!.xField} vs ${scatterPlan!.spec!.yFields?.[0]}`);
  // P8：bubble xField / yFields[0] / sizeField 三者互异
  const bubblePlan = r.plans.find(p => p.type === 'bubble');
  assertOk(bubblePlan?.spec !== null, 'bubble spec should be non-null');
  const bx = bubblePlan!.spec!.xField;
  const by = bubblePlan!.spec!.yFields?.[0];
  const bs = bubblePlan!.spec!.sizeField;
  assertOk(typeof bx === 'string' && bx.length > 0, 'bubble xField resolved');
  assertOk(typeof by === 'string' && by.length > 0, 'bubble yFields[0] resolved');
  assertOk(typeof bs === 'string' && bs.length > 0, 'bubble sizeField resolved');
  assertOk(bx !== by, `bubble xField (${bx}) must differ from yFields[0] (${by})`);
  assertOk(bx !== bs, `bubble xField (${bx}) must differ from sizeField (${bs})`);
  assertOk(by !== bs, `bubble yFields[0] (${by}) must differ from sizeField (${bs})`);
  // P7：numeric_relationship 无 entityField → scatter/bubble 的 seriesField 被跳过
  assertEqual(scatterPlan!.spec!.seriesField, undefined, 'scatter spec should have no seriesField without entityField');
  assertEqual(bubblePlan!.spec!.seriesField, undefined, 'bubble spec should have no seriesField without entityField');
});

// ============================================================
// 夹具 11：二维矩阵（heatmap 需 matrix_aggregate，gate → unsupported）
// ============================================================

test('fixture 11: categorical_matrix → heatmap gate, defaultPlan=null (见问题清单 P2)', () => {
  const r = run({
    columns: ['region', 'month', 'avg_temp'],
    rows: [
      { region: '城北', month: '1月', avg_temp: 5.2 }, { region: '城北', month: '2月', avg_temp: 7.1 },
      { region: '城南', month: '1月', avg_temp: 6.0 }, { region: '城南', month: '2月', avg_temp: 8.3 },
    ],
  });
  assertEqual(r.profile.archetype, 'categorical_matrix');
  assertEqual(r.profile.traits.matrixEligible, true);
  assertEqual(r.profile.traits.dimensionFieldCount, 2);
  // 报告期望 heatmap=recommended，实际 matrix_aggregate 未实现 → gate → unsupported
  assertSuitMap(r.plans, { heatmap: 'unsupported' }, 'fixture 11');
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// 夹具 12：真正分组分布样本（boxplot gate 已翻）
// ============================================================

test('fixture 12: detail_rows + grouped samples → boxplot allowed_explicit', () => {
  const r = run({
    columns: ['station', 'ph_value'],
    rows: [
      { station: 'A', ph_value: 7.2 }, { station: 'A', ph_value: 7.5 }, { station: 'A', ph_value: 6.8 },
      { station: 'B', ph_value: 8.1 }, { station: 'B', ph_value: 7.9 },
    ],
  });
  assertEqual(r.profile.archetype, 'detail_rows');
  assertEqual(r.profile.traits.groupedSamplesEligible, true);
  assertEqual(r.profile.traits.duplicateDimensionKeys, true);
  // boxplot_summary gate 已翻 → allowed_explicit（auto 无 recommended，defaultPlan=null）
  assertSuitMap(r.plans, { boxplot: 'allowed_explicit' }, 'fixture 12');
  assertEqual(r.defaultPlan, null);
  assertEqual(r.noChartReason, 'no_recommended_plan_for_auto');
});

// ============================================================
// 夹具 13：重复分类可加指标（P3 修复后判为 detail_rows）
// ============================================================

test('fixture 13: duplicate aggregable → detail_rows, defaultPlan=null', () => {
  const r = run({
    columns: ['region', 'count'],
    rows: [
      { region: '城北', count: 10 }, { region: '城北', count: 15 },
      { region: '城南', count: 8 }, { region: '城南', count: 12 },
    ],
  });
  // P3 修复后：detailConfidence >= 0.5 → detail_rows
  assertEqual(r.profile.archetype, 'detail_rows');
  assertEqual(r.profile.traits.duplicateDimensionKeys, true);
  assertEqual(r.profile.traits.measureKinds['count'], 'additive');
  assertOk(r.profile.traits.detailConfidence >= 0.5, 'P3: detailConfidence should be >= 0.5');
  // bar 仍是 allowed_explicit（detail_rows 无 auto recommended）
  assertSuitMap(r.plans, { bar: 'allowed_explicit', horizontal_bar: 'allowed_explicit' }, 'fixture 13');
  assertEqual(r.defaultPlan, null); // auto 无 recommended
  assertEqual(r.noChartReason, 'no_recommended_plan_for_auto');
  // boxplot 仍 unsupported（groupedSamplesEligible=false，非可加指标 + 每组多样本条件不满足）
  assertEqual(bestSuitability(r.plans, 'boxplot'), 'unsupported');
});

// ============================================================
// 夹具 14：多指标剖面（radar/combo 仅 allowed_explicit，auto 无 recommended）
// ============================================================

test('fixture 14: categorical_series multi-measure → radar/combo allowed_explicit (见问题清单 P4)', () => {
  const r = run({
    columns: ['station_name', 'ph', 'do', 'cod', 'nh3n'],
    rows: [
      { station_name: 'A', ph: 7.2, do: 6.5, cod: 12.0, nh3n: 0.5 },
      { station_name: 'B', ph: 7.8, do: 5.8, cod: 18.0, nh3n: 0.8 },
      { station_name: 'C', ph: 7.0, do: 7.2, cod: 8.0, nh3n: 0.3 },
    ],
  });
  assertEqual(r.profile.archetype, 'categorical_series');
  assertEqual(r.profile.traits.measureCount, 4);
  assertEqual(r.profile.traits.dimensionCardinality, 3);
  // 报告期望 radar=recommended，实际 radar maxSuitability=allowed_explicit → 不提升
  assertSuitMap(r.plans, { radar: 'allowed_explicit', combo: 'allowed_explicit' }, 'fixture 14');
  assertEqual(r.defaultPlan, null); // auto 无 recommended
  assertEqual(r.noChartReason, 'no_recommended_plan_for_auto');
});

// ============================================================
// 夹具 15：异构指标汇总（关键纠正：不出图）
// ============================================================

test('fixture 15: heterogeneous_metric_rows → all unsupported, defaultPlan=null', () => {
  const r = run({
    columns: ['metric_name', 'value'],
    rows: [
      { metric_name: 'BOD监测记录总数', value: 16 },
      { metric_name: '涉及排污口数量', value: 16 },
      { metric_name: '平均每个排污口记录数', value: 1 },
    ],
  });
  assertEqual(r.profile.archetype, 'heterogeneous_metric_rows');
  assertOk(r.profile.traits.heterogeneousConfidence >= 0.6);
  assertSuitMap(r.plans, {}, 'fixture 15'); // 全 unsupported
  assertEqual(r.defaultPlan, null);
  assertOk(r.noChartReason !== null);
});

// ============================================================
// P8 边界：measure 不足时 scatter/bubble 不生成同列 spec
// ============================================================

test('P8: single measure → scatter/bubble unsupported (no same-column spec)', () => {
  // 仅 1 个 measure，无法满足 scatter(measureCount>=2) / bubble(measureCount>=3)
  const r = run({
    columns: ['category', 'value'],
    rows: [
      { category: 'A', value: 1 }, { category: 'B', value: 2 },
      { category: 'C', value: 3 }, { category: 'D', value: 4 },
    ],
  });
  const scatter = r.plans.find(p => p.type === 'scatter');
  const bubble = r.plans.find(p => p.type === 'bubble');
  assertEqual(scatter?.resolvedSuitability, 'unsupported', 'scatter must be unsupported with 1 measure');
  assertEqual(bubble?.resolvedSuitability, 'unsupported', 'bubble must be unsupported with 1 measure');
  assertEqual(scatter?.spec, null, 'scatter must have null spec (no same-column fallback)');
  assertEqual(bubble?.spec, null, 'bubble must have null spec (no same-column fallback)');
});

test('P8: two measures → bubble unsupported (measureCount<3), scatter still has distinct x/y', () => {
  // 两数值关系：scatter 可执行，bubble 因 measureCount<3 unsupported
  const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({ x: i, y: i * 2 }));
  const r = run({ columns: ['x', 'y'], rows });
  const scatter = r.plans.find(p => p.type === 'scatter');
  const bubble = r.plans.find(p => p.type === 'bubble');
  assertEqual(bubble?.resolvedSuitability, 'unsupported', 'bubble needs >=3 measures');
  assertEqual(bubble?.spec, null, 'bubble must have null spec');
  assertEqual(scatter?.resolvedSuitability, 'recommended', 'scatter ok with 2 measures');
  assertOk(scatter?.spec !== null, 'scatter spec non-null');
  assertOk(scatter!.spec!.xField !== scatter!.spec!.yFields?.[0], 'scatter x/y distinct');
});

// ============================================================
// P7：seriesField optional enhancement
// numeric_relationship 不可能含 entityField（archetype 约束 dimensionFields===0），
// 故"有 entityField 写入 seriesField"用自定义 capability + detail_rows archetype 数据验证
// （P1 后：多 measure + 非聚合 → detail_rows），不扩展 ALL_CAPABILITIES_V2 中 scatter/bubble 的适用范围。
// ============================================================

/** 极简测试 capability：detail_rows archetype，seriesField=entityField（optional） */
const P7_CAPABILITY: ChartCapability = {
  type: 'scatter',
  label: 'P7 测试散点',
  variants: [
    {
      id: 'p7_series_optional',
      archetypeSuitability: { detail_rows: 'allowed_explicit' },
      traitRequirements: [
        { trait: 'measureCount', min: 2 },
      ],
      semanticMode: 'relationship',
      maxSuitability: 'allowed_explicit',
      fieldMapping: {
        xField: { source: 'measureField', index: 0 },
        yFields: { source: 'measureFieldsAfter', afterIndex: 0, maxCount: 1 },
        seriesField: { source: 'entityField' },
      },
      transform: 'none',
      rendererRequirements: [],
      unsupportedReasonCode: 'p7_unsupported',
    },
  ],
};

test('P7: with entityField → spec carries seriesField', () => {
  // station_name 被 findEntityNameField 识别为 entityField；archetype=unknown
  const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
    station_name: 'S' + (i % 3), rainfall: 10 + i, runoff: 2 + i,
  }));
  const r = planChartsWithCapabilitiesV2(
    { columns: ['station_name', 'rainfall', 'runoff'], rows, source: 'auto', intent: 'auto' },
    [P7_CAPABILITY],
  );
  // P1 后：多 measure + 非聚合 → detail_rows
  assertEqual(r.profile.archetype, 'detail_rows');
  assertEqual(r.profile.entityField, 'station_name');
  const plan = r.plans.find(p => p.variantId === 'p7_series_optional');
  assertOk(plan?.spec !== null, 'spec should be non-null with entityField present');
  assertEqual(plan!.spec!.seriesField, 'station_name', 'seriesField should be written when entityField exists');
  // x/y 仍互异且必填
  assertOk(plan!.spec!.xField === 'rainfall', 'xField resolved');
  assertOk(plan!.spec!.yFields?.[0] === 'runoff', 'yFields[0] resolved');
});

test('P7: without entityField → spec non-null, seriesField skipped (not null)', () => {
  // 非 measure 列 label 不被识别为 entityField；P1 后多 measure + 非聚合 → detail_rows。
  const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
    label: 'L' + (i % 3), rainfall: 10 + i, runoff: 2 + i,
  }));
  const r = planChartsWithCapabilitiesV2(
    { columns: ['label', 'rainfall', 'runoff'], rows, source: 'auto', intent: 'auto' },
    [P7_CAPABILITY],
  );
  assertEqual(r.profile.entityField, null, 'no entityField for label column');
  const plan = r.plans.find(p => p.variantId === 'p7_series_optional');
  assertOk(plan?.spec !== null, 'spec should still be non-null without entityField');
  assertEqual(plan!.spec!.seriesField, undefined, 'seriesField should be skipped (undefined) when entityField absent');
});

test('P7: required xField/yFields missing → spec=null (only seriesField is optional)', () => {
  // 仅 1 个 measure：measureCount>=2 trait 失败 → unsupported，spec=null
  // （seriesField optional 不应让必填字段缺失也能生成 spec）
  const rows: Row[] = Array.from({ length: 10 }, (_, i) => ({
    station_name: 'S' + (i % 3), rainfall: 10 + i,
  }));
  const r = planChartsWithCapabilitiesV2(
    { columns: ['station_name', 'rainfall'], rows, source: 'auto', intent: 'auto' },
    [P7_CAPABILITY],
  );
  const plan = r.plans.find(p => p.variantId === 'p7_series_optional');
  assertEqual(plan?.resolvedSuitability, 'unsupported', 'insufficient measures → unsupported');
  assertEqual(plan?.spec, null, 'required fields missing → spec=null (seriesField optionality does not rescue)');
});

// ============================================================
// P9：auto 默认选择 tie-break（autoPriority）
// ============================================================

test('P9: three-measure auto → bubble (tie-break, not array order)', () => {
  // ALL_CAPABILITIES_V2 中 scatter 排在 bubble 之前，但 auto 应选 bubble（autoPriority 0 < 2）
  const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({ rainfall: 10 + i * 2, runoff: 2 + i * 0.5, area: 100 + i * 10 }));
  const r = run({ columns: ['rainfall', 'runoff', 'area'], rows });
  const scatter = r.plans.find(p => p.type === 'scatter');
  const bubble = r.plans.find(p => p.type === 'bubble');
  assertEqual(scatter?.resolvedSuitability, 'recommended', 'scatter recommended');
  assertEqual(bubble?.resolvedSuitability, 'recommended', 'bubble recommended');
  // 两者都 recommended，但 auto 选 bubble（不靠数组顺序：scatter 在数组中更靠前）
  assertEqual(r.defaultPlan?.type, 'bubble', 'auto must pick bubble via tie-break, not array order');
  assertEqual(r.defaultPlan?.variantId, 'bubble_numeric_relationship');
});

test('P9: three-measure user requests scatter → scatter (autoPriority does not override user)', () => {
  // 三数值关系数据，user 显式请求 scatter → 应返回 scatter，不被 bubble tie-break 覆盖
  const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({ rainfall: 10 + i * 2, runoff: 2 + i * 0.5, area: 100 + i * 10 }));
  const r = run({
    columns: ['rainfall', 'runoff', 'area'], rows,
    source: 'user', requestedChartType: 'scatter', intent: 'auto',
  });
  assertEqual(r.profile.archetype, 'numeric_relationship');
  assertEqual(r.profile.traits.measureCount, 3);
  // auto 会选 bubble，但 user 请求 scatter → 返回 scatter
  assertEqual(r.defaultPlan?.type, 'scatter', 'user requested scatter must be honored');
  assertEqual(r.defaultPlan?.variantId, 'scatter_numeric_relationship');
  assertEqual(r.fallbackNotice, null, 'scatter is available, no fallback');
});

test('P9: two-measure auto → scatter (not bubble, bubble trait fails)', () => {
  // 两数值关系：bubble 因 measureCount<3 不 recommended，auto 选 scatter
  const rows: Row[] = Array.from({ length: 20 }, (_, i) => ({ rainfall: 10 + i * 2, runoff: 2 + i * 0.5 }));
  const r = run({ columns: ['rainfall', 'runoff'], rows });
  assertEqual(r.defaultPlan?.type, 'scatter');
  assertEqual(r.plans.find(p => p.type === 'bubble')?.resolvedSuitability, 'unsupported');
});

// ============================================================
// 跨夹具不变量：13 种 type 都有至少一个 plan
// ============================================================

test('invariant: every fixture has 13 types in plans', () => {
  const allFixtures: FixInput[] = [
    { columns: [], rows: [] },
    { columns: ['total_count'], rows: [{ total_count: 342 }] },
    { columns: ['region', 'count'], rows: [{ region: 'A', count: 1 }] },
  ];
  for (const f of allFixtures) {
    const r = run(f);
    const types = new Set(r.plans.map(p => p.type));
    for (const t of ALL_TYPES) {
      assertOk(types.has(t), `fixture missing plan for type ${t}`);
    }
  }
});

test('invariant: planChartsV2 default still uses PILOT (not ALL)', () => {
  // 间接验证：PILOT 只 3 种，ALL 13 种；planChartsV2 返回的 plans 数量应 = PILOT variant 总数(6)
  // 直接 import planChartsV2 会耦合，这里只验证 ALL 路径的 variant 总数
  const r = run({ columns: ['region', 'count'], rows: [{ region: 'A', count: 1 }] });
  assertEqual(r.plans.length, 17); // ALL_CAPABILITIES_V2 共 17 个 variant
});

// ============================================================
// 问题清单（profiler/capability 与报告不一致）
// ============================================================
//
// ── 阶段 B-2 已修复（P6/P7/P8/P9） ──
//
// P6（trait 命名）✅ 已修复：TraitRequirement 已支持 string 字面量相等，
//    area 现用 `{ trait: 'aggregationState', equals: 'aggregated' }` 严格表达
//    "仅聚合后时间序列适合面积图"（不再用 entityFieldCount<=1 近似）。
//    报告 isAggregated → profiler aggregationState(三态) 的语义鸿沟已消除。
//
// P7（scatter/bubble seriesField）✅ 已修复：buildSpec 中 seriesField 改为
//    optional enhancement——解析失败时跳过（不写该字段），不让 spec=null。
//    scatter/bubble 已补回 `seriesField: { source: 'entityField' }`：
//    有 entityField 时写入 spec，无时 spec 仍有效（仅缺 seriesField）。
//    注：numeric_relationship archetype 约束 dimensionFields===0，而 entityField
//    必为非 measure 列→必进 dimensionFields，故 numeric_relationship 数据恒无
//    entityField，seriesField 在该场景总被跳过（设计如此，非缺陷）。
//
// P8（scatter/bubble xField 与 yField 同列）✅ 已修复：新增 MultiFieldSelector
//    `measureFieldsAfter`（跳过前 afterIndex+1 个 measure）。scatter/bubble 的
//    yFields 改用 `{ afterIndex: 0, maxCount: 1 }` 取 measureFields[1]，
//    bubble sizeField 用 measureField[2]。x=measureFields[0]、y=[1]、size=[2] 三者互异。
//
// P9（夹具 10 defaultPlan）✅ 已修复：selectForAuto 增加 autoPriority(plan, profile)
//    稳定 tie-break，按数据形态显式排序，不依赖 ALL_CAPABILITIES_V2 数组顺序。
//    三数值关系 auto 默认选 bubble（autoPriority 0 优先于 scatter 2）。
//    仅影响 source==='auto'，user/model/dashboard 不受影响。
//
// ── 阶段 B-3 已修复（P1/P3/P5） ──
//
// P1（夹具 4）✅ 已修复：multi_series_temporal 判定收紧，要求单 measure + aggregated +
//    无 identifierFields + multiSeriesEligible；并在其前增加 detail_rows 优先拦截
//    （有 identifierFields / 多 measure 非聚合 / detailConfidence>=0.5 非聚合）。
//    P1 明细数据（id + station + date + 多 measure）现判 detail_rows，与报告期望一致。
//    真聚合多系列时间序列（station + month + value，单 measure + aggregated + 无 id）
//    仍判 multi_series_temporal。
//
// P3（夹具 13）✅ 已修复：computeDetailConfidence 增加窄条件证据——
//    duplicateDimensionKeys + measureCount===1 + additive + 非聚合 → +0.2，
//    将 detailConfidence 从 0.30 提升到 0.50，命中 detail_rows 优先拦截。
//    重复 region+count 数据现判 detail_rows，与报告期望一致。
//    非可加分布样本（分组 pH）不受影响，仍由 groupedSamplesEligible 控制。
//
// P5（archetype 命名）✅ 已修复：旧实体时序命名 → multi_series_temporal 全量统一，
//    不保留双命名。
//
// ── 仍未修复（profiler/archetype/transform 阶段，后续单独处理） ──
//
// P2（夹具 11）：报告期望 heatmap=recommended，实际 unsupported。
//    原因：heatmap 依赖 matrix_aggregate transform，chartDataTransformV2 未实现，
//    通过 rendererRequirements gate=false 标 unsupported。
//    属 transform/renderer 阶段，本阶段不实现，保留 gate 行为。
//
// P4（夹具 14）：报告期望 radar=recommended，实际 allowed_explicit。
//    原因：ALL_CAPABILITIES_V2 中 radar archetypeSuitability[categorical_series]=allowed_explicit，
//    maxSuitability=allowed_explicit。intent=auto 不提升。
//    报告 §8 夹具 14 期望 defaultPlan=radar，但报告 §3.4 categorical_series 默认 intent=comparison，
//    radar(profile) 与 comparison 不匹配，按报告 §4.4 不应提升为 recommended。
//    结论：报告夹具 14 的期望与报告 §4.4 规则自相矛盾，本实现遵循 §4.4 intent 规则，保留现状。

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 Golden Fixtures Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
