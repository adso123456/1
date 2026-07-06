// shadowComparisonV2.test.ts — V2 Shadow Comparison 离线对比
//
// 对同一份 Golden 夹具数据，分别运行旧逻辑（getChartTypeAvailability + pickDefault）
// 和 V2 逻辑（planChartsWithCapabilitiesV2 + ALL_CAPABILITIES_V2），
// 比较默认图表类型选择，输出差异分类报告。
//
// 不接入运行时。不修改任何生产代码。

import { getChartTypeAvailability, isRenderableChartType } from '../chartRegistry.js';
import { planChartsWithCapabilitiesV2 } from '../chartPlannerV2.js';
import { ALL_CAPABILITIES_V2 } from '../chartCapabilityV2.js';
import type { ChartData, ChartTypeAvailability, RenderableChartType } from '../types.js';
import type { Row } from '../datasetProfilerV2.js';
import { FIXTURES } from './goldenFixtures.js';

// ============================================================
// 差异分类
// ============================================================

type DiffCategory = 'same' | 'accepted' | 'known_issue' | 'watch';

interface ComparisonRow {
  fixture: string;
  archetype: string;
  oldDefault: string | null;
  oldSupportedCount: number;
  v2Default: string | null;
  v2SupportedCount: number;
  category: DiffCategory;
  note: string;
}

// ============================================================
// 旧 pickDefault 简化版（复制自 ChartView.tsx:83-112）
// 模拟 auto 路径：explicitType=false，无模型偏好
// ============================================================

/**
 * 从 getChartTypeAvailability 的结果中按 ChartView 相同逻辑选取默认类型。
 * 与 ChartView.tsx pickDefault() 的区别：
 *   - explicitType 恒为 false（auto 路径）
 *   - modelType 使用 spec.type（模拟 fallbackSpecFromColumns 的默认值）
 *   - 当无任何 supported 类型时返回 null（而非 'bar'），
 *     因为 'bar' 此时 unsupported，buildChartOption 返回 null，用户看到错误提示。
 */
function pickDefaultLikeChartView(
  availabilities: ChartTypeAvailability[],
  modelType: string = 'bar',
): RenderableChartType | null {
  // Step 1: explicitType — auto 路径恒为 false，跳过

  // Step 2: 模型指定类型在 suitability=recommended 时作为默认
  if (isRenderableChartType(modelType as RenderableChartType)) {
    const modelRec = availabilities.find(
      t => t.type === modelType && t.supported && t.suitability === 'recommended',
    );
    if (modelRec) return modelRec.type;
  }

  // Step 3: 第一个 supported 且 recommended 的类型
  const firstRecommended = availabilities.find(
    t => t.supported && t.suitability === 'recommended',
  );
  if (firstRecommended) return firstRecommended.type;

  // Step 4: 没有 recommended → 模型指定且 supported 的类型
  if (isRenderableChartType(modelType as RenderableChartType)) {
    const modelSupported = availabilities.find(
      t => t.type === modelType && t.supported,
    );
    if (modelSupported) return modelSupported.type;
  }

  // Step 5: 第一个 supported 类型
  const firstSupported = availabilities.find(t => t.supported);
  if (firstSupported) return firstSupported.type;

  // 无任何 supported → 返回 null（等同于"不出图"）
  return null;
}

// ============================================================
// 比较逻辑
// ============================================================

/**
 * 对单个夹具运行旧逻辑和 V2 逻辑，返回对比行。
 */
function compareOne(
  name: string,
  columns: string[],
  rows: Row[],
): ComparisonRow {
  // ── 旧逻辑 ──
  // 模拟 auto 路径的 ChartData：spec.type='bar'（与 fallbackSpecFromColumns 一致），
  // explicitType=false（auto），无 chart_spec 偏好。
  const chartData: ChartData = {
    id: 'shadow-test',
    columns,
    rows,
    spec: { type: 'bar' },
    title: 'Shadow Comparison',
    dataVersion: 0,
  };

  const availabilities = getChartTypeAvailability(chartData);
  const oldDefault = pickDefaultLikeChartView(availabilities, 'bar');
  const oldSupported = availabilities.filter(a => a.supported);

  // ── V2 逻辑 ──
  const v2Result = planChartsWithCapabilitiesV2(
    { columns, rows, source: 'auto', intent: 'auto' },
    ALL_CAPABILITIES_V2,
  );
  const v2Default = v2Result.defaultPlan?.type ?? null;
  const v2Supported = v2Result.plans.filter(
    p => p.resolvedSuitability !== 'unsupported',
  );

  // ── 差异分类 ──
  const archetype = v2Result.profile.archetype;

  // same：双方默认类型一致（或双方都是 null）
  if (oldDefault === v2Default) {
    return {
      fixture: name,
      archetype,
      oldDefault,
      oldSupportedCount: oldSupported.length,
      v2Default,
      v2SupportedCount: v2Supported.length,
      category: 'same',
      note: '',
    };
  }

  // V2 不出图（defaultPlan=null）但旧逻辑出图 → 需要判断是否 accepted
  if (v2Default === null && oldDefault !== null) {
    // accepted：V2 正确识别为不适合自动出图的数据形态
    if (
      archetype === 'detail_rows' ||
      archetype === 'heterogeneous_metric_rows' ||
      archetype === 'empty'
    ) {
      return {
        fixture: name,
        archetype,
        oldDefault,
        oldSupportedCount: oldSupported.length,
        v2Default,
        v2SupportedCount: v2Supported.length,
        category: 'accepted',
        note: `V2 判 ${archetype}，不应自动出图（旧逻辑误推 ${oldDefault}）`,
      };
    }
    // accepted：multi_series_temporal + renderer gate=false → V2 正确不出图
    // 旧逻辑 getChartTypeAvailability 用 pie/bar 的试错 spec 蒙混通过，
    // 实际上多实体时间序列无法用单系列图表正确表达。
    if (archetype === 'multi_series_temporal') {
      return {
        fixture: name,
        archetype,
        oldDefault,
        oldSupportedCount: oldSupported.length,
        v2Default,
        v2SupportedCount: v2Supported.length,
        category: 'accepted',
        note: `V2 判 ${archetype}，multi-series gate=false，不应自动出图（旧逻辑误推 ${oldDefault}）`,
      };
    }
    // heatmap gate 已翻（B-7D）→ categorical_matrix 不再全 unsupported
    // auto 仍无 recommended（heatmap 仅 allowed_explicit），defaultPlan=null 符合预期
    if (archetype === 'categorical_matrix') {
      return {
        fixture: name,
        archetype,
        oldDefault,
        oldSupportedCount: oldSupported.length,
        v2Default,
        v2SupportedCount: v2Supported.length,
        category: 'accepted',
        note: `V2 判 ${archetype}，heatmap 仅 allowed_explicit，auto 不应自动出图（旧逻辑误推 ${oldDefault}）`,
      };
    }
    // known_issue：P4 — categorical_series 多指标 V2 无 recommended（radar 仅 allowed_explicit）
    // 旧逻辑 getChartTypeAvailability 的 bar（核心计划）可能 supported，
    // 但 V2 要求单指标 categorical_series 才有 recommended，多指标仅 radar/combo allowed_explicit。
    if (
      archetype === 'categorical_series' &&
      v2Supported.length > 0 &&
      v2Result.plans.every(p => p.resolvedSuitability !== 'recommended')
    ) {
      return {
        fixture: name,
        archetype,
        oldDefault,
        oldSupportedCount: oldSupported.length,
        v2Default,
        v2SupportedCount: v2Supported.length,
        category: 'known_issue',
        note: `P4: categorical_series 多指标 V2 无 recommended（radar 仅 allowed_explicit），auto 无默认`,
      };
    }
    // watch：未解释的差异
    return {
      fixture: name,
      archetype,
      oldDefault,
      oldSupportedCount: oldSupported.length,
      v2Default,
      v2SupportedCount: v2Supported.length,
      category: 'watch',
      note: `V2 null vs old ${oldDefault} (archetype=${archetype}) — 未预期差异`,
    };
  }

  // V2 出图但旧逻辑不出图 → watch（V2 不应比旧逻辑更宽松）
  if (v2Default !== null && oldDefault === null) {
    return {
      fixture: name,
      archetype,
      oldDefault,
      oldSupportedCount: oldSupported.length,
      v2Default,
      v2SupportedCount: v2Supported.length,
      category: 'watch',
      note: `V2 选 ${v2Default} 但旧逻辑不出图 — 未预期差异`,
    };
  }

  // 双方都出图但类型不同
  // accepted：V2 numeric_relationship 选 scatter/bubble，旧逻辑可能误推 pie/bar
  // （旧 getChartTypeAvailability 试错法会把数值列当分类列，构造出 pie spec 并通过验证）
  if (
    archetype === 'numeric_relationship' &&
    (v2Default === 'scatter' || v2Default === 'bubble')
  ) {
    return {
      fixture: name,
      archetype,
      oldDefault,
      oldSupportedCount: oldSupported.length,
      v2Default,
      v2SupportedCount: v2Supported.length,
      category: 'accepted',
      note: `V2 正确选 ${v2Default}（数值关系数据），旧逻辑误推 ${oldDefault}（试错法缺陷）`,
    };
  }

  // heatmap gate 已翻（B-7D），此分支不应再触发（V2 heatmap 现在可实现）
  if (archetype === 'categorical_matrix' && oldDefault === 'heatmap') {
    return {
      fixture: name,
      archetype,
      oldDefault,
      oldSupportedCount: oldSupported.length,
      v2Default,
      v2SupportedCount: v2Supported.length,
      category: 'watch',
      note: `P2 已修复但未预期: old=heatmap, v2=${v2Default}`,
    };
  }

  // watch：fallthrough — 未分类的差异
  return {
    fixture: name,
    archetype,
    oldDefault,
    oldSupportedCount: oldSupported.length,
    v2Default,
    v2SupportedCount: v2Supported.length,
    category: 'watch',
    note: `类型不同: old=${oldDefault} v2=${v2Default} archetype=${archetype}`,
  };
}

// ============================================================
// 运行全部对比
// ============================================================

// 覆盖至少 13 个关键夹具
const TARGET_FIXTURES = [
  'empty',
  'single_kpi',
  'region_count',
  'month_discharge',
  'monitoring_detail_with_id',
  'station_ph_samples',
  'repeated_region_count',
  'complete_multi_series_temporal',
  'incomplete_multi_series_temporal',
  'two_numeric',
  'three_numeric',
  'region_month_matrix',
  'station_multi_measure',
  'heterogeneous_metric_rows',
];

const rows: ComparisonRow[] = [];

for (const name of TARGET_FIXTURES) {
  const f = FIXTURES.find(x => x.name === name);
  if (!f) {
    console.error(`Fixture not found: ${name}`);
    continue;
  }
  rows.push(compareOne(f.name, f.columns, f.rows));
}

// ============================================================
// 结果统计
// ============================================================

const same = rows.filter(r => r.category === 'same');
const accepted = rows.filter(r => r.category === 'accepted');
const knownIssues = rows.filter(r => r.category === 'known_issue');
const watch = rows.filter(r => r.category === 'watch');

console.log(`\n${'='.repeat(72)}`);
console.log(`V2 Shadow Comparison Report`);
console.log(`${'='.repeat(72)}`);
console.log(`Fixtures: ${rows.length}`);
console.log(`  same:         ${same.length}`);
console.log(`  accepted:     ${accepted.length}`);
console.log(`  known_issue:  ${knownIssues.length}`);
console.log(`  watch:        ${watch.length}`);
console.log(`${'='.repeat(72)}`);

// 打印详细表格
console.log(`\n${'Fixture'.padEnd(34)} ${'Old'.padEnd(10)} ${'V2'.padEnd(10)} ${'Category'.padEnd(13)} Archetype`);
console.log('-'.repeat(100));
for (const r of rows) {
  const oldStr = r.oldDefault ?? 'null';
  const v2Str = r.v2Default ?? 'null';
  console.log(
    `${r.fixture.padEnd(34)} ${oldStr.padEnd(10)} ${v2Str.padEnd(10)} ${r.category.padEnd(13)} ${r.archetype}`,
  );
}

// 打印差异说明
const diffs = rows.filter(r => r.category !== 'same');
if (diffs.length > 0) {
  console.log(`\n--- 差异说明 ---`);
  for (const r of diffs) {
    console.log(`  [${r.category}] ${r.fixture}: ${r.note}`);
  }
}

// watch 打印 console.table
if (watch.length > 0) {
  console.log(`\n!!! WATCH 差异（未预期）!!!`);
  console.table(watch.map(r => ({
    fixture: r.fixture,
    oldDefault: r.oldDefault,
    v2Default: r.v2Default,
    archetype: r.archetype,
  })));
}

// ============================================================
// 显式断言
// ============================================================

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, msg: string): void {
  if (actual !== expected) {
    failed++;
    console.error(`\nFAIL: ${msg}`);
    console.error(`  expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  } else {
    passed++;
  }
}

function assertOk(cond: boolean, msg: string): void {
  if (!cond) {
    failed++;
    console.error(`\nFAIL: ${msg}`);
  } else {
    passed++;
  }
}

// ── 行数 ≥ 12 ──
assertOk(rows.length >= 12, `rows.length=${rows.length} should be >= 12`);

// ── watch 必须为 0 ──
assertOk(watch.length === 0, `watch count=${watch.length} should be 0`);

// ── 关键夹具断言 ──

// single_kpi → V2 默认 gauge
{
  const r = rows.find(x => x.fixture === 'single_kpi')!;
  assertEqual(r.v2Default, 'gauge', 'single_kpi: V2 默认 gauge');
}

// region_count → V2 默认 bar
{
  const r = rows.find(x => x.fixture === 'region_count')!;
  assertEqual(r.v2Default, 'bar', 'region_count: V2 默认 bar');
}

// month_discharge → V2 默认 line
{
  const r = rows.find(x => x.fixture === 'month_discharge')!;
  assertEqual(r.v2Default, 'line', 'month_discharge: V2 默认 line');
}

// monitoring_detail_with_id → V2 默认 null，分类 accepted
{
  const r = rows.find(x => x.fixture === 'monitoring_detail_with_id')!;
  assertEqual(r.v2Default, null, 'monitoring_detail_with_id: V2 默认 null');
  assertEqual(r.category, 'accepted', 'monitoring_detail_with_id: 分类 accepted');
}

// repeated_region_count → V2 默认 null，分类 accepted
{
  const r = rows.find(x => x.fixture === 'repeated_region_count')!;
  assertEqual(r.v2Default, null, 'repeated_region_count: V2 默认 null');
  assertEqual(r.category, 'accepted', 'repeated_region_count: 分类 accepted');
}

// two_numeric → V2 默认 scatter
{
  const r = rows.find(x => x.fixture === 'two_numeric')!;
  assertEqual(r.v2Default, 'scatter', 'two_numeric: V2 默认 scatter');
}

// three_numeric → V2 默认 bubble
{
  const r = rows.find(x => x.fixture === 'three_numeric')!;
  assertEqual(r.v2Default, 'bubble', 'three_numeric: V2 默认 bubble');
}

// region_month_matrix → known_issue 或 V2 默认 null
{
  const r = rows.find(x => x.fixture === 'region_month_matrix')!;
  const ok = r.category === 'known_issue' || r.v2Default === null;
  assertOk(ok, `region_month_matrix: known_issue or v2Default=null, got category=${r.category} v2Default=${r.v2Default}`);
}

// heterogeneous_metric_rows → V2 默认 null（全 unsupported）
{
  const r = rows.find(x => x.fixture === 'heterogeneous_metric_rows')!;
  assertEqual(r.v2Default, null, 'heterogeneous_metric_rows: V2 默认 null');
}

// complete_multi_series_temporal → V2 默认 null（gate=false）
{
  const r = rows.find(x => x.fixture === 'complete_multi_series_temporal')!;
  assertEqual(r.v2Default, null, 'complete_multi_series_temporal: V2 默认 null（multi-series gate）');
}

// ── 所有 accepted 行必须带 note ──
for (const r of accepted) {
  assertOk(r.note.length > 0, `${r.fixture}: accepted 行必须有 note`);
}

// ── 所有 known_issue 行必须带 note ──
for (const r of knownIssues) {
  assertOk(r.note.length > 0, `${r.fixture}: known_issue 行必须有 note`);
}

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(72)}`);
console.log(`Shadow Comparison Assertions`);
console.log(`${'='.repeat(72)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(72)}`);

if (failed > 0) {
  throw new Error(`${failed} assertion(s) failed`);
}
