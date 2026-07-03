// datasetProfilerV2.test.ts — V2 Dataset Profiler 的 15 个 Golden 测试
//
// 使用内联断言，无外部测试框架依赖。

import { analyzeDatasetV2, type DatasetProfileV2 } from '../datasetProfilerV2.js';
import { FIXTURES } from './goldenFixtures.js';

let passed = 0;
let failed = 0;

// ---- 内联断言（避免依赖 @types/node） ----

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

function profile(name: string): DatasetProfileV2 {
  const f = FIXTURES.find(x => x.name === name);
  if (!f) throw new Error(`Fixture not found: ${name}`);
  return analyzeDatasetV2(f.columns, f.rows);
}

// ============================================================
// 1. 空数据
// ============================================================
test('empty → archetype=empty', () => {
  const p = profile('empty');
  assertEqual(p.archetype, 'empty');
  assertEqual(p.traits.measureCount, 0);
  assertEqual(p.traits.dimensionFieldCount, 0);
  assertEqual(p.traits.rowCount, 0);
});

// ============================================================
// 2. 单 KPI
// ============================================================
test('single_kpi → archetype=single_value', () => {
  const p = profile('single_kpi');
  assertEqual(p.archetype, 'single_value');
  assertEqual(p.traits.measureCount, 1);
  assertEqual(p.traits.rowCount, 1);
  assertEqual(p.traits.dimensionFieldCount, 0);
  assertEqual(p.measureFields[0], 'total_count');
});

// ============================================================
// 3. 单行多指标
// ============================================================
test('single_row_multi_measure → archetype=single_row_multi_measure', () => {
  const p = profile('single_row_multi_measure');
  assertEqual(p.archetype, 'single_row_multi_measure');
  assertEqual(p.traits.measureCount, 3);
  assertEqual(p.traits.rowCount, 1);
});

// ============================================================
// 4. 带唯一 ID 的监测明细
// ============================================================
test('monitoring_detail_with_id → archetype=detail_rows', () => {
  const p = profile('monitoring_detail_with_id');
  assertEqual(p.archetype, 'detail_rows');
  assertOk(p.traits.detailConfidence >= 0.5,
    `detailConfidence=${p.traits.detailConfidence} should be >= 0.5`);
  assertEqual(p.traits.aggregationState, 'raw');
  assertEqual(p.identifierFields.length, 1);
  assertEqual(p.identifierFields[0], 'sample_id');
  assertOk(p.traits.detailEvidence.some((e: string) => e.includes('标识字段')),
    'detailEvidence should mention identifier field');
  // 无 entity 字段（location_code 不匹配实体关键词）
  assertEqual(p.entityField, null);
  assertEqual(p.traits.entityFieldCount, 0);
});

// ============================================================
// 5. region + count
// ============================================================
test('region_count → archetype=categorical_series', () => {
  const p = profile('region_count');
  assertEqual(p.archetype, 'categorical_series');
  assertEqual(p.traits.measureCount, 1);
  assertEqual(p.traits.dimensionFieldCount, 1);
  assertEqual(p.traits.dimensionCardinality, 5);
  assertEqual(p.traits.duplicateDimensionKeys, false);
  assertEqual(p.traits.primaryDimensionHasDuplicates, false);
  assertEqual(p.traits.aggregationState, 'aggregated');
  assertEqual(p.traits.partToWholeEligible, true);
  assertEqual(p.traits.groupedSamplesEligible, false);
  assertEqual(p.traits.matrixEligible, false);
  assertEqual(p.traits.measureKinds['count'], 'additive');
});

// ============================================================
// 6. month + discharge (单序列时间趋势)
// ============================================================
test('month_discharge → archetype=temporal_series', () => {
  const p = profile('month_discharge');
  assertEqual(p.archetype, 'temporal_series');
  assertEqual(p.traits.measureCount, 1);
  assertEqual(p.traits.temporalFieldCount, 1);
  assertOk(p.traits.timePointCount >= 2);
  assertEqual(p.traits.dimensionFieldCount, 1);
  assertEqual(p.traits.entityFieldCount, 0);
  assertEqual(p.traits.entityCount, 0);
  assertEqual(p.traits.duplicateDimensionKeys, false);
  assertEqual(p.traits.aggregationState, 'aggregated');
  // discharge 英文名不命中中文 non_additive 规则，classifyMeasureKindV2 返回 unknown
  assertEqual(p.traits.measureKinds['discharge'], 'unknown');
  // unknown kind 不阻止 partToWhole（只有 non_additive 阻止）
  assertEqual(p.traits.partToWholeEligible, true);
});

// ============================================================
// 7. 完整多实体时间数据
// ============================================================
test('complete_multi_entity_temporal → multi_entity_temporal (complete=1.0)', () => {
  const p = profile('complete_multi_entity_temporal');
  assertEqual(p.archetype, 'multi_entity_temporal');
  assertEqual(p.traits.entityFieldCount, 1);
  assertOk(p.traits.entityCount >= 2);
  assertEqual(p.traits.temporalFieldCount, 1);
  assertOk(p.traits.timePointCount >= 2);
  assertEqual(p.traits.multiSeriesEligible, true);
  assertOk(p.traits.multiSeriesCompleteness >= 0.99,
    `multiSeriesCompleteness=${p.traits.multiSeriesCompleteness} should be ~1.0`);
  assertEqual(p.traits.duplicateDimensionKeys, false);
  assertEqual(p.traits.primaryDimensionHasDuplicates, true);
});

// ============================================================
// 8. 不完整多实体时间数据
// ============================================================
test('incomplete_multi_entity_temporal → multi_entity_temporal (complete<1.0)', () => {
  const p = profile('incomplete_multi_entity_temporal');
  assertEqual(p.archetype, 'multi_entity_temporal');
  assertEqual(p.traits.entityFieldCount, 1);
  assertEqual(p.traits.entityCount, 2);
  assertEqual(p.traits.temporalFieldCount, 1);
  assertOk(p.traits.timePointCount >= 2);
  assertEqual(p.traits.multiSeriesEligible, true);
  assertOk(p.traits.multiSeriesCompleteness < 0.99,
    `multiSeriesCompleteness=${p.traits.multiSeriesCompleteness} should be < 1.0`);
  assertEqual(p.traits.duplicateDimensionKeys, false);
});

// ============================================================
// 9. 两数值关系
// ============================================================
test('two_numeric → archetype=numeric_relationship', () => {
  const p = profile('two_numeric');
  assertEqual(p.archetype, 'numeric_relationship');
  assertEqual(p.traits.measureCount, 2);
  assertEqual(p.traits.dimensionFieldCount, 0);
  assertEqual(p.traits.temporalFieldCount, 0);
});

// ============================================================
// 10. 三数值关系
// ============================================================
test('three_numeric → archetype=numeric_relationship', () => {
  const p = profile('three_numeric');
  assertEqual(p.archetype, 'numeric_relationship');
  assertEqual(p.traits.measureCount, 3);
  assertEqual(p.traits.dimensionFieldCount, 0);
  assertEqual(p.traits.temporalFieldCount, 0);
});

// ============================================================
// 11. region + month + value (二维矩阵)
// ============================================================
test('region_month_matrix → archetype=categorical_matrix', () => {
  const p = profile('region_month_matrix');
  assertEqual(p.archetype, 'categorical_matrix');
  assertEqual(p.traits.measureCount, 1);
  assertEqual(p.traits.dimensionFieldCount, 2);
  // 关键：主维度有重复但完整组合唯一
  assertEqual(p.traits.primaryDimensionHasDuplicates, true);
  assertEqual(p.traits.duplicateDimensionKeys, false);
  assertEqual(p.traits.matrixEligible, true);
  // month 同时是时间字段和矩阵维度
  assertEqual(p.traits.temporalFieldCount, 1);
});

test('fixture 11 matrix trait cross-check', () => {
  const p = profile('region_month_matrix');
  assertOk(p.traits.dimensionFields.includes('region'));
  assertOk(p.traits.dimensionFields.includes('month'));
  assertOk(p.temporalFields.includes('month'));
  assertOk(p.traits.uniqueDimensionPairRatio >= 0.99,
    `uniqueDimensionPairRatio=${p.traits.uniqueDimensionPairRatio} should be ~1.0`);
});

// ============================================================
// 12. station + 多条 pH 样本
// ============================================================
test('station_ph_samples → detail_rows, groupedSamplesEligible=true', () => {
  const p = profile('station_ph_samples');
  assertEqual(p.archetype, 'detail_rows');
  assertOk(p.traits.detailConfidence >= 0.5,
    `detailConfidence=${p.traits.detailConfidence} should be >= 0.5`);
  assertEqual(p.traits.groupedSamplesEligible, true);
  assertEqual(p.traits.primaryDimensionHasDuplicates, true);
  assertEqual(p.traits.duplicateDimensionKeys, true);
  assertEqual(p.traits.measureKinds['ph_value'], 'non_additive');
  assertOk(p.traits.detailEvidence.some((e: string) => e.includes('分组样本')),
    `detailEvidence: ${p.traits.detailEvidence.join('; ')}`);
  assertEqual(p.traits.aggregationState, 'raw');
});

// ============================================================
// 13. 重复 region + count
// ============================================================
test('repeated_region_count → unknown, groupedSamplesEligible=false', () => {
  const p = profile('repeated_region_count');
  assertEqual(p.archetype, 'unknown');
  assertOk(p.traits.detailConfidence < 0.5,
    `detailConfidence=${p.traits.detailConfidence} should be < 0.5`);
  assertEqual(p.traits.groupedSamplesEligible, false);
  assertEqual(p.traits.measureKinds['count'], 'additive');
  assertEqual(p.traits.primaryDimensionHasDuplicates, true);
  assertEqual(p.traits.duplicateDimensionKeys, true);
  assertEqual(p.traits.aggregationState, 'raw');
});

// ============================================================
// 14. station + 多指标
// ============================================================
test('station_multi_measure → categorical_series', () => {
  const p = profile('station_multi_measure');
  assertEqual(p.archetype, 'categorical_series');
  assertEqual(p.traits.measureCount, 4);
  assertEqual(p.traits.dimensionFieldCount, 1);
  assertEqual(p.traits.dimensionCardinality, 3);
  assertEqual(p.traits.duplicateDimensionKeys, false);
  assertEqual(p.traits.primaryDimensionHasDuplicates, false);
  assertEqual(p.traits.aggregationState, 'aggregated');
  assertEqual(p.entityField, 'station_name');
  assertEqual(p.traits.entityFieldCount, 1);
  assertEqual(p.traits.entityCount, 3);
  assertEqual(p.traits.temporalFieldCount, 0);
  assertEqual(p.traits.multiSeriesEligible, false);
});

// ============================================================
// 15. metric_name + value 异构汇总
// ============================================================
test('heterogeneous_metric_rows → archetype=heterogeneous_metric_rows', () => {
  const p = profile('heterogeneous_metric_rows');
  assertEqual(p.archetype, 'heterogeneous_metric_rows');
  assertOk(p.traits.heterogeneousConfidence >= 0.6,
    `heterogeneousConfidence=${p.traits.heterogeneousConfidence} should be >= 0.6`);
  assertEqual(p.traits.measureCount, 1);
  assertEqual(p.traits.dimensionFieldCount, 1);
  assertEqual(p.traits.dimensionCardinality, 3);
  assertOk(p.traits.heterogeneousEvidence.some((e: string) => e.includes('统计口径')),
    `heterogeneousEvidence: ${p.traits.heterogeneousEvidence.join('; ')}`);
  assertOk(p.traits.heterogeneousEvidence.some((e: string) => e.toLowerCase().includes('metric')),
    `heterogeneousEvidence should include dim name evidence`);
});

// ============================================================
// 额外交叉验证
// ============================================================
test('cross: region_count aggregation evidence', () => {
  const p = profile('region_count');
  assertOk(p.traits.aggregationEvidence.some((e: string) => e.includes('聚合特征')),
    `aggregationEvidence: ${p.traits.aggregationEvidence.join('; ')}`);
});

test('cross: monitoring detail has raw aggregation', () => {
  const p = profile('monitoring_detail_with_id');
  assertOk(p.traits.aggregationEvidence.some((e: string) => e.includes('标识字段')),
    `aggregationEvidence: ${p.traits.aggregationEvidence.join('; ')}`);
});

test('cross: ph_value is non_additive', () => {
  const p = profile('station_ph_samples');
  assertEqual(p.traits.measureKinds['ph_value'], 'non_additive');
});

test('cross: count is additive for region_count', () => {
  const p = profile('region_count');
  assertEqual(p.traits.measureKinds['count'], 'additive');
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 Dataset Profiler Golden Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  // 使用抛出来退出非零（避免 process.exit 依赖 @types/node）
  throw new Error(`${failed} test(s) failed`);
}
