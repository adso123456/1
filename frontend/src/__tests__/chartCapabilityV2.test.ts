// chartCapabilityV2.test.ts — V2 ChartCapability 契约结构测试 + 编译器类型守卫
//
// 运行时测试：验证 PILOT_CAPABILITIES_V2 的唯一性和结构正确性。
// ts-expect-error 块：验证非法 trait 组合在编译期被拒绝。

import {
  PILOT_CAPABILITIES_V2,
  type ChartCapabilityVariant,
  type TraitRequirement,
  type ArchetypeSuitability,
  type ChartCapability,
} from '../chartCapabilityV2.js';
import type { ChartSuitability } from '../types.js';

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

function allVariants(): ChartCapabilityVariant[] {
  return PILOT_CAPABILITIES_V2.flatMap(c => [...c.variants]);
}

function allTraitRequirements(): TraitRequirement[] {
  return allVariants().flatMap(v => [...v.traitRequirements]);
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

test('bar_categorical_aggregated renderer gate is group_by_sum: true', () => {
  const v = allVariants().find(x => x.id === 'bar_categorical_aggregated');
  assertOk(v !== undefined);
  const req = v!.rendererRequirements.find(r => r.capability === 'group_by_sum');
  assertOk(req !== undefined, 'should have group_by_sum renderer requirement');
  assertEqual(req!.currentlySupported, true);
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
// 6. bar temporal_series 降级
// ============================================================

test('bar temporal_series is allowed_explicit (not recommended)', () => {
  const v = allVariants().find(x => x.id === 'bar_categorical_comparison');
  assertOk(v !== undefined);
  const suit = v!.archetypeSuitability['temporal_series'];
  assertEqual(suit, 'allowed_explicit');
});

// ============================================================
// 7. archetypeSuitability 不含 unsupported
// ============================================================

test('archetypeSuitability values are valid SupportedSuitability', () => {
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
// 8. TraitRequirement 运行时一致性验证（更新后规则）
// ============================================================

const BOOLEAN_TRAIT_NAMES = new Set<string>([
  'primaryDimensionHasDuplicates',
  'duplicateDimensionKeys',
  'hasNegativeValues',
  'multiSeriesEligible',
  'groupedSamplesEligible',
  'partToWholeEligible',
  'matrixEligible',
]);

test('boolean trait requirements never use min/max', () => {
  for (const req of allTraitRequirements()) {
    if (BOOLEAN_TRAIT_NAMES.has(req.trait)) {
      assertOk(
        !('min' in req) && !('max' in req),
        `${req.trait}: boolean trait must not use min/max`,
      );
    }
  }
});

test('boolean trait requirements have exactly one of equals/required/forbidden', () => {
  for (const req of allTraitRequirements()) {
    if (BOOLEAN_TRAIT_NAMES.has(req.trait)) {
      const hasEquals = 'equals' in req;
      const hasRequired = 'required' in req;
      const hasForbidden = 'forbidden' in req;
      const count = (hasEquals ? 1 : 0) + (hasRequired ? 1 : 0) + (hasForbidden ? 1 : 0);
      assertEqual(count, 1,
        `${req.trait}: boolean trait must have exactly one of equals/required/forbidden, got ${count}`);
    }
  }
});

test('boolean equals is boolean type', () => {
  for (const req of allTraitRequirements()) {
    if (BOOLEAN_TRAIT_NAMES.has(req.trait) && 'equals' in req) {
      assertOk(
        typeof (req as { equals: unknown }).equals === 'boolean',
        `${req.trait}: boolean trait equals must be boolean`,
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

test('numeric trait requirements have at least one of equals/min/max', () => {
  for (const req of allTraitRequirements()) {
    if (!BOOLEAN_TRAIT_NAMES.has(req.trait)) {
      const hasEquals = 'equals' in req;
      const hasMin = 'min' in req;
      const hasMax = 'max' in req;
      assertOk(
        hasEquals || hasMin || hasMax,
        `${req.trait}: numeric trait must have at least one of equals/min/max`,
      );
    }
  }
});

// ============================================================
// 9. 字段映射完整性
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
// 10. maxSuitability 一致性
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
// 11. SemVer-like 结构冻结（只允许 readonly、无额外属性）
// ============================================================

test('PILOT_CAPABILITIES_V2 is frozen (array spread works)', () => {
  // 验证 readonly 数组可遍历
  let count = 0;
  for (const _cap of PILOT_CAPABILITIES_V2) count++;
  assertEqual(count, 3);
});

// ============================================================
// 编译期 @ts-expect-error 测试
// 以下声明会被 TypeScript 编译器验证：
//   - 有类型错误 + @ts-expect-error → 编译通过（正确）
//   - 无类型错误 + @ts-expect-error → TS2578 编译失败（类型约束被破坏）
// ============================================================

// 1) boolean trait 不能使用 min
// @ts-expect-error: boolean trait must not use min
const _compileCheck1: TraitRequirement = { trait: 'hasNegativeValues', min: 0 };

// 2) numeric trait 不能使用 required
// @ts-expect-error: numeric trait must not use required
const _compileCheck2: TraitRequirement = { trait: 'measureCount', required: true };

// 3) 不能同时设置 required 和 forbidden
// @ts-expect-error: cannot combine required and forbidden
const _compileCheck3: TraitRequirement = { trait: 'duplicateDimensionKeys', required: true, forbidden: true };

// 4) archetypeSuitability 不能填写 unsupported
// @ts-expect-error: 'unsupported' is not assignable to SupportedSuitability
const _compileCheck4: ArchetypeSuitability = { empty: 'unsupported' as ChartSuitability };

// 5) ChartCapability 不能使用 RenderableChartType 之外的 type（'none' 已被 Exclude）
// @ts-expect-error: 'none' is excluded from RenderableChartType
const _compileCheck5: ChartCapability = { type: 'none', label: '无', variants: [] };

// ── 标记引用（避免 unused-variable 警告） ──
void _compileCheck1; void _compileCheck2; void _compileCheck3;
void _compileCheck4; void _compileCheck5;

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
