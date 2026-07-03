// chartCapabilityV2.test.ts — V2 ChartCapability 契约结构测试
//
// 验证 PILOT_CAPABILITIES_V2 的类型安全、唯一性和结构正确性。

import {
  PILOT_CAPABILITIES_V2,
  type ChartCapabilityVariant,
  type TraitRequirement,
} from '../chartCapabilityV2.js';

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

/** 收集所有 variant */
function allVariants(): ChartCapabilityVariant[] {
  return PILOT_CAPABILITIES_V2.flatMap(c => c.variants);
}

/** 收集所有 trait requirement */
function allTraitRequirements(): TraitRequirement[] {
  return allVariants().flatMap(v => v.traitRequirements);
}

// ============================================================
// 1. 试点范围
// ============================================================

test('only 3 pilot capabilities', () => {
  assertEqual(PILOT_CAPABILITIES_V2.length, 3);
  const types = PILOT_CAPABILITIES_V2.map(c => c.type).sort();
  assertEqual(types.join(','), 'bar,boxplot,line');
});

// ============================================================
// 2. type 与 variant id 唯一性
// ============================================================

test('capability type is unique', () => {
  const types = PILOT_CAPABILITIES_V2.map(c => c.type);
  assertEqual(new Set(types).size, types.length, `duplicate type found: ${types}`);
});

test('variant id is globally unique', () => {
  const ids = allVariants().map(v => v.id);
  assertEqual(new Set(ids).size, ids.length, `duplicate id found: ${ids}`);
});

// ============================================================
// 3. 各 capability 的 variant 列表
// ============================================================

test('bar has 2 variants', () => {
  const bar = PILOT_CAPABILITIES_V2.find(c => c.type === 'bar');
  assertOk(bar !== undefined);
  const ids = bar!.variants.map(v => v.id).sort();
  assertEqual(ids.join(','), 'bar_categorical_aggregated,bar_categorical_comparison');
});

test('line has 3 variants', () => {
  const line = PILOT_CAPABILITIES_V2.find(c => c.type === 'line');
  assertOk(line !== undefined);
  assertEqual(line!.variants.length, 3);
  const ids = line!.variants.map(v => v.id).sort();
  assertEqual(ids.join(','), 'line_categorical_comparison,line_temporal_trend_multi,line_temporal_trend_single');
});

test('boxplot has 1 variant', () => {
  const box = PILOT_CAPABILITIES_V2.find(c => c.type === 'boxplot');
  assertOk(box !== undefined);
  assertEqual(box!.variants.length, 1);
  assertEqual(box!.variants[0].id, 'boxplot_grouped_distribution');
});

// ============================================================
// 4. transform 正确性
// ============================================================

test('bar_categorical_comparison transform is none', () => {
  const v = allVariants().find(x => x.id === 'bar_categorical_comparison');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'none');
});

test('bar_categorical_aggregated transform is group_by_sum', () => {
  const v = allVariants().find(x => x.id === 'bar_categorical_aggregated');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'group_by_sum');
});

test('line_temporal_trend_single transform is none', () => {
  const v = allVariants().find(x => x.id === 'line_temporal_trend_single');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'none');
});

test('line_temporal_trend_multi transform is none', () => {
  const v = allVariants().find(x => x.id === 'line_temporal_trend_multi');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'none');
});

test('line_categorical_comparison transform is none', () => {
  const v = allVariants().find(x => x.id === 'line_categorical_comparison');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'none');
});

test('boxplot_grouped_distribution transform is boxplot_summary', () => {
  const v = allVariants().find(x => x.id === 'boxplot_grouped_distribution');
  assertOk(v !== undefined);
  assertEqual(v!.transform, 'boxplot_summary');
});

// ============================================================
// 5. renderer 门槛正确性
// ============================================================

test('bar_categorical_aggregated renderer gate is group_by_sum: false', () => {
  const v = allVariants().find(x => x.id === 'bar_categorical_aggregated');
  assertOk(v !== undefined);
  const req = v!.rendererRequirements.find(r => r.capability === 'group_by_sum');
  assertOk(req !== undefined, 'should have group_by_sum renderer requirement');
  assertEqual(req!.currentlySupported, false);
});

test('line_temporal_trend_multi renderer gate is multi_series_line: false', () => {
  const v = allVariants().find(x => x.id === 'line_temporal_trend_multi');
  assertOk(v !== undefined);
  const req = v!.rendererRequirements.find(r => r.capability === 'multi_series_line');
  assertOk(req !== undefined, 'should have multi_series_line renderer requirement');
  assertEqual(req!.currentlySupported, false);
});

test('boxplot_grouped_distribution renderer gate is boxplot_summary: false', () => {
  const v = allVariants().find(x => x.id === 'boxplot_grouped_distribution');
  assertOk(v !== undefined);
  const req = v!.rendererRequirements.find(r => r.capability === 'boxplot_summary');
  assertOk(req !== undefined, 'should have boxplot_summary renderer requirement');
  assertEqual(req!.currentlySupported, false);
});

test('recommended variants have no renderer gates', () => {
  for (const cap of PILOT_CAPABILITIES_V2) {
    for (const v of cap.variants) {
      if (v.maxSuitability === 'recommended') {
        assertEqual(
          v.rendererRequirements.length,
          0,
          `${v.id}: recommended variant should have no renderer gates`,
        );
      }
    }
  }
});

// ============================================================
// 6. archetypeSuitability 不含 unsupported
// ============================================================

test('archetypeSuitability values are valid SupportedSuitability', () => {
  // SupportedSuitability = 'recommended' | 'allowed_explicit'（编译期已排除 'unsupported'）
  const valid = new Set(['recommended', 'allowed_explicit']);
  for (const v of allVariants()) {
    for (const [archetype, suitability] of Object.entries(v.archetypeSuitability)) {
      assertOk(
        valid.has(suitability),
        `${v.id}: archetype "${archetype}" has invalid suitability "${suitability}"`,
      );
    }
  }
});

test('every variant has at least one archetype entry', () => {
  for (const v of allVariants()) {
    const entries = Object.keys(v.archetypeSuitability);
    assertOk(entries.length > 0, `${v.id}: archetypeSuitability must not be empty`);
  }
});

// ============================================================
// 7. TraitRequirement 运行时一致性验证
// ============================================================

/** 从 DatasetTraitsV2 接口静态推导的 boolean trait 名称集合（硬编码副本，用于运行时校验） */
const BOOLEAN_TRAIT_NAMES = new Set<string>([
  'primaryDimensionHasDuplicates',
  'duplicateDimensionKeys',
  'hasNegativeValues',
  'multiSeriesEligible',
  'groupedSamplesEligible',
  'partToWholeEligible',
  'matrixEligible',
]);

test('boolean trait requirements never use min/max/equals', () => {
  for (const req of allTraitRequirements()) {
    if (BOOLEAN_TRAIT_NAMES.has(req.trait)) {
      assertOk(
        !('min' in req) && !('max' in req) && !('equals' in req),
        `${req.trait}: boolean trait must not use min/max/equals`,
      );
    }
  }
});

test('numeric trait requirements never use required/forbidden', () => {
  for (const req of allTraitRequirements()) {
    if (!BOOLEAN_TRAIT_NAMES.has(req.trait)) {
      assertOk(
        !('required' in req) && !('forbidden' in req),
        `${req.trait}: numeric trait must not use required/forbidden`,
      );
    }
  }
});

// ============================================================
// 8. 字段映射完整性（关键 selector 存在）
// ============================================================

test('every variant has xField mapping', () => {
  for (const v of allVariants()) {
    assertOk(v.fieldMapping.xField !== undefined, `${v.id}: xField is required`);
  }
});

test('every non-boxplot variant has yFields mapping', () => {
  for (const v of allVariants()) {
    if (v.id === 'boxplot_grouped_distribution') continue;
    assertOk(v.fieldMapping.yFields !== undefined, `${v.id}: yFields is required`);
  }
});

test('boxplot has valueField mapping', () => {
  const v = allVariants().find(x => x.id === 'boxplot_grouped_distribution');
  assertOk(v !== undefined);
  assertOk(v!.fieldMapping.valueField !== undefined, 'boxplot should have valueField');
});

// ============================================================
// 9. maxSuitability 一致性
// ============================================================

test('transform !== none implies maxSuitability is allowed_explicit', () => {
  for (const v of allVariants()) {
    if (v.transform !== 'none') {
      assertEqual(v.maxSuitability, 'allowed_explicit',
        `${v.id}: transform=${v.transform} should cap maxSuitability to allowed_explicit`);
    }
  }
});

test('renderer gate exists implies maxSuitability is allowed_explicit', () => {
  for (const v of allVariants()) {
    if (v.rendererRequirements.length > 0) {
      assertEqual(v.maxSuitability, 'allowed_explicit',
        `${v.id}: has renderer gates, maxSuitability should be allowed_explicit`);
    }
  }
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 ChartCapability Contract Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
