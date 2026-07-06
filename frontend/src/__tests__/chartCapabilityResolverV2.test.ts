// chartCapabilityResolverV2.test.ts — V2 Resolver 契约测试
//
// 运行时测试：matcher 布尔/数值语义、selector 解析、边界情况。
// ts-expect-error 块：验证非法输入在编译期被拒绝。

import {
  matchTraitRequirement,
  matchTraitRequirements,
  resolveScalarSelector,
  resolveMultiSelector,
  type FieldSelectorContext,
} from '../chartCapabilityResolverV2.js';
import type { TraitRequirement } from '../chartCapabilityV2.js';
import type { DatasetProfileV2, DatasetTraitsV2 } from '../datasetProfilerV2.js';
import type { ChartSpec } from '../types.js';

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

function assertDeepEqual<T>(actual: T, expected: T, msg?: string): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(
      msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
    );
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

// ============================================================
// 工厂函数
// ============================================================

function defaultTraits(): DatasetTraitsV2 {
  return {
    dimensionFields: [],
    primaryDimensionField: null,
    dimensionCardinality: 0,
    primaryDimensionHasDuplicates: false,
    duplicateDimensionKeys: false,
    uniqueDimensionPairRatio: 0,
    aggregationState: 'unknown',
    aggregationEvidence: [],
    measureCount: 0,
    dimensionFieldCount: 0,
    temporalFieldCount: 0,
    entityFieldCount: 0,
    numericFieldCount: 0,
    rowCount: 0,
    categoryCardinality: 0,
    entityCount: 0,
    timePointCount: 0,
    maxEntityOccurrence: 0,
    hasNegativeValues: false,
    multiSeriesEligible: false,
    multiSeriesCompleteness: 0,
    groupedSamplesEligible: false,
    partToWholeEligible: false,
    matrixEligible: false,
    measureKinds: {},
    measureTotals: {},
    detailConfidence: 0,
    detailEvidence: [],
    heterogeneousConfidence: 0,
    heterogeneousEvidence: [],
  };
}

function traits(overrides: Partial<DatasetTraitsV2>): DatasetTraitsV2 {
  return { ...defaultTraits(), ...overrides };
}

function defaultProfile(): DatasetProfileV2 {
  return {
    columns: [],
    rowCount: 0,
    numericFields: [],
    temporalFields: [],
    measureFields: [],
    identifierFields: [],
    entityField: null,
    regionField: null,
    archetype: 'unknown',
    traits: defaultTraits(),
  };
}

function profile(overrides: Partial<DatasetProfileV2>): DatasetProfileV2 {
  return {
    ...defaultProfile(),
    ...overrides,
    traits: overrides.traits ?? defaultTraits(),
  };
}

function ctx(p: DatasetProfileV2, spec?: ChartSpec): FieldSelectorContext {
  return { profile: p, preferredSpec: spec };
}

// ============================================================
// 1. Boolean Trait Matcher — equals
// ============================================================

test('boolean equals true → match', () => {
  const t = traits({ hasNegativeValues: true });
  assertOk(matchTraitRequirement({ trait: 'hasNegativeValues', equals: true }, t));
});

test('boolean equals true → mismatch', () => {
  const t = traits({ hasNegativeValues: false });
  assertOk(!matchTraitRequirement({ trait: 'hasNegativeValues', equals: true }, t));
});

test('boolean equals false → match', () => {
  const t = traits({ hasNegativeValues: false });
  assertOk(matchTraitRequirement({ trait: 'hasNegativeValues', equals: false }, t));
});

test('boolean equals false → mismatch', () => {
  const t = traits({ hasNegativeValues: true });
  assertOk(!matchTraitRequirement({ trait: 'hasNegativeValues', equals: false }, t));
});

// ============================================================
// 2. Boolean Trait Matcher — required
// ============================================================

test('boolean required true → match when true', () => {
  const t = traits({ multiSeriesEligible: true });
  assertOk(matchTraitRequirement({ trait: 'multiSeriesEligible', required: true }, t));
});

test('boolean required true → mismatch when false', () => {
  const t = traits({ multiSeriesEligible: false });
  assertOk(!matchTraitRequirement({ trait: 'multiSeriesEligible', required: true }, t));
});

// ============================================================
// 3. Boolean Trait Matcher — forbidden
// ============================================================

test('boolean forbidden true → match when false', () => {
  const t = traits({ duplicateDimensionKeys: false });
  assertOk(matchTraitRequirement({ trait: 'duplicateDimensionKeys', forbidden: true }, t));
});

test('boolean forbidden true → mismatch when true', () => {
  const t = traits({ duplicateDimensionKeys: true });
  assertOk(!matchTraitRequirement({ trait: 'duplicateDimensionKeys', forbidden: true }, t));
});

// ============================================================
// 4. Numeric Trait Matcher — equals
// ============================================================

test('numeric equals → match', () => {
  const t = traits({ measureCount: 3 });
  assertOk(matchTraitRequirement({ trait: 'measureCount', equals: 3 }, t));
});

test('numeric equals → mismatch', () => {
  const t = traits({ measureCount: 5 });
  assertOk(!matchTraitRequirement({ trait: 'measureCount', equals: 3 }, t));
});

// ============================================================
// 5. Numeric Trait Matcher — min
// ============================================================

test('numeric min only → value above min passes', () => {
  const t = traits({ dimensionCardinality: 10 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', min: 1 }, t));
});

test('numeric min only → value equals min passes', () => {
  const t = traits({ dimensionCardinality: 1 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', min: 1 }, t));
});

test('numeric min only → value below min fails', () => {
  const t = traits({ dimensionCardinality: 0 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', min: 1 }, t));
});

test('numeric min null → unbounded below passes', () => {
  const t = traits({ dimensionCardinality: 0 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', min: null }, t));
});

// ============================================================
// 6. Numeric Trait Matcher — max
// ============================================================

test('numeric max only → value below max passes', () => {
  const t = traits({ dimensionCardinality: 10 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', max: 20 }, t));
});

test('numeric max only → value equals max passes', () => {
  const t = traits({ dimensionCardinality: 20 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', max: 20 }, t));
});

test('numeric max only → value above max fails', () => {
  const t = traits({ dimensionCardinality: 30 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', max: 20 }, t));
});

test('numeric max null → unbounded above passes', () => {
  const t = traits({ dimensionCardinality: 999 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', max: null }, t));
});

// ============================================================
// 7. Numeric Trait Matcher — min + max 组合
// ============================================================

test('numeric min+max → value within range passes', () => {
  const t = traits({ dimensionCardinality: 5 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', min: 2, max: 20 }, t));
});

test('numeric min+max → value below min fails', () => {
  const t = traits({ dimensionCardinality: 1 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', min: 2, max: 20 }, t));
});

test('numeric min+max → value above max fails', () => {
  const t = traits({ dimensionCardinality: 30 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', min: 2, max: 20 }, t));
});

test('numeric max+min → value within range passes', () => {
  const t = traits({ dimensionCardinality: 5 });
  assertOk(matchTraitRequirement({ trait: 'dimensionCardinality', max: 20, min: 2 }, t));
});

test('numeric max+min → value below min fails', () => {
  const t = traits({ dimensionCardinality: 1 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', max: 20, min: 2 }, t));
});

test('numeric max+min → value above max fails', () => {
  const t = traits({ dimensionCardinality: 30 });
  assertOk(!matchTraitRequirement({ trait: 'dimensionCardinality', max: 20, min: 2 }, t));
});

// ============================================================
// 7b. Numeric Trait Matcher — equals + min + max 运行时组合
// （TraitRequirement 类型层面不允许三者共存，但运行时需防御）
// ============================================================

test('numeric equals+min+max all satisfied → passes', () => {
  const t = traits({ dimensionCardinality: 5 });
  // 运行时对象包含 equals + min + max 三字段（通过类型断言绕过编译检查）
  const req = { trait: 'dimensionCardinality' as const, equals: 5, min: 2, max: 20 };
  assertOk(matchTraitRequirement(req as TraitRequirement, t));
});

test('numeric equals satisfied but min fails → fails', () => {
  const t = traits({ dimensionCardinality: 1 });
  const req = { trait: 'dimensionCardinality' as const, equals: 1, min: 2 };
  assertOk(!matchTraitRequirement(req as TraitRequirement, t));
});

test('numeric equals satisfied but max fails → fails', () => {
  const t = traits({ dimensionCardinality: 30 });
  const req = { trait: 'dimensionCardinality' as const, equals: 30, max: 20 };
  assertOk(!matchTraitRequirement(req as TraitRequirement, t));
});

// ============================================================
// 7d. String Trait Matcher — equals（字面量相等，如 aggregationState）
// ============================================================

test('string equals → match when value equals literal', () => {
  const t = traits({ aggregationState: 'aggregated' });
  assertOk(matchTraitRequirement({ trait: 'aggregationState', equals: 'aggregated' }, t));
});

test('string equals → mismatch when value differs', () => {
  const t = traits({ aggregationState: 'raw' });
  assertOk(!matchTraitRequirement({ trait: 'aggregationState', equals: 'aggregated' }, t));
});

test('string equals → mismatch when value is unknown', () => {
  const t = traits({ aggregationState: 'unknown' });
  assertOk(!matchTraitRequirement({ trait: 'aggregationState', equals: 'aggregated' }, t));
});

test('string equals → raw literal matches raw', () => {
  const t = traits({ aggregationState: 'raw' });
  assertOk(matchTraitRequirement({ trait: 'aggregationState', equals: 'raw' }, t));
});

test('string trait defaultTraits aggregationState is unknown → aggregated fails', () => {
  // defaultTraits 出厂的 aggregationState='unknown'，不应误判为 aggregated
  const t = defaultTraits();
  assertOk(!matchTraitRequirement({ trait: 'aggregationState', equals: 'aggregated' }, t));
});

// ============================================================
// 7c. Numeric Trait Matcher — NaN / 非有限数
// ============================================================

test('numeric trait value NaN → fails', () => {
  const t = traits({ measureCount: NaN });
  assertOk(!matchTraitRequirement({ trait: 'measureCount', equals: 1 }, t));
});

test('numeric trait value Infinity → fails', () => {
  const t = traits({ measureCount: Infinity });
  assertOk(!matchTraitRequirement({ trait: 'measureCount', min: 1 }, t));
});

test('numeric trait value -Infinity → fails', () => {
  const t = traits({ measureCount: -Infinity });
  assertOk(!matchTraitRequirement({ trait: 'measureCount', max: 100 }, t));
});

// ============================================================
// 8. 其他 boolean trait 交叉验证
// ============================================================

test('primaryDimensionHasDuplicates: required true matches', () => {
  assertOk(matchTraitRequirement(
    { trait: 'primaryDimensionHasDuplicates', required: true },
    traits({ primaryDimensionHasDuplicates: true }),
  ));
});

test('groupedSamplesEligible: required true matches', () => {
  assertOk(matchTraitRequirement(
    { trait: 'groupedSamplesEligible', required: true },
    traits({ groupedSamplesEligible: true }),
  ));
});

test('partToWholeEligible: forbidden true matches when false', () => {
  assertOk(matchTraitRequirement(
    { trait: 'partToWholeEligible', forbidden: true },
    traits({ partToWholeEligible: false }),
  ));
});

test('matrixEligible: equals false matches', () => {
  assertOk(matchTraitRequirement(
    { trait: 'matrixEligible', equals: false },
    traits({ matrixEligible: false }),
  ));
});

// ============================================================
// 9. 其他 numeric trait 交叉验证
// ============================================================

test('entityFieldCount equals 0 matches', () => {
  const t = traits({ entityFieldCount: 0 });
  assertOk(matchTraitRequirement({ trait: 'entityFieldCount', equals: 0 }, t));
});

test('timePointCount min 2 matches when 5', () => {
  const t = traits({ timePointCount: 5 });
  assertOk(matchTraitRequirement({ trait: 'timePointCount', min: 2 }, t));
});

test('rowCount max 100 matches when 50', () => {
  const t = traits({ rowCount: 50 });
  assertOk(matchTraitRequirement({ trait: 'rowCount', max: 100 }, t));
});

// ============================================================
// 10. matchTraitRequirements — 多条件
// ============================================================

test('empty requirements → passes', () => {
  const t = traits({ measureCount: 0 });
  assertOk(matchTraitRequirements([], t));
});

test('all conditions met → passes', () => {
  const t = traits({
    measureCount: 1,
    dimensionCardinality: 5,
    duplicateDimensionKeys: false,
  });
  assertOk(matchTraitRequirements([
    { trait: 'measureCount', equals: 1 },
    { trait: 'dimensionCardinality', min: 1 },
    { trait: 'duplicateDimensionKeys', forbidden: true },
  ], t));
});

test('one condition fails → fails', () => {
  const t = traits({
    measureCount: 1,
    dimensionCardinality: 0,
    duplicateDimensionKeys: false,
  });
  assertOk(!matchTraitRequirements([
    { trait: 'measureCount', equals: 1 },
    { trait: 'dimensionCardinality', min: 1 },
    { trait: 'duplicateDimensionKeys', forbidden: true },
  ], t));
});

test('all conditions fail → fails', () => {
  const t = traits({
    measureCount: 3,
    dimensionCardinality: 0,
    duplicateDimensionKeys: true,
  });
  assertOk(!matchTraitRequirements([
    { trait: 'measureCount', equals: 1 },
    { trait: 'dimensionCardinality', min: 1 },
    { trait: 'duplicateDimensionKeys', forbidden: true },
  ], t));
});

// ============================================================
// 11. resolveScalarSelector — Profiler 独有字段
// ============================================================

test('entityField selector returns entityField', () => {
  const p = profile({ entityField: 'company_name' });
  assertEqual(resolveScalarSelector({ source: 'entityField' }, ctx(p)), 'company_name');
});

test('entityField selector returns null when null', () => {
  const p = profile({ entityField: null });
  assertEqual(resolveScalarSelector({ source: 'entityField' }, ctx(p)), null);
});

test('regionField selector returns regionField', () => {
  const p = profile({ regionField: 'city' });
  assertEqual(resolveScalarSelector({ source: 'regionField' }, ctx(p)), 'city');
});

test('regionField selector returns null when null', () => {
  const p = profile({ regionField: null });
  assertEqual(resolveScalarSelector({ source: 'regionField' }, ctx(p)), null);
});

// ============================================================
// 12. resolveScalarSelector — traits 字段
// ============================================================

test('primaryDimensionField selector returns primaryDimensionField', () => {
  const p = profile({ traits: traits({ primaryDimensionField: 'product' }) });
  assertEqual(
    resolveScalarSelector({ source: 'primaryDimensionField' }, ctx(p)),
    'product',
  );
});

test('primaryDimensionField selector returns null when null', () => {
  const p = profile({ traits: traits({ primaryDimensionField: null }) });
  assertEqual(
    resolveScalarSelector({ source: 'primaryDimensionField' }, ctx(p)),
    null,
  );
});

test('dimensionField selector with index 0 returns first dimension', () => {
  const p = profile({ traits: traits({ dimensionFields: ['d1', 'd2', 'd3'] }) });
  assertEqual(
    resolveScalarSelector({ source: 'dimensionField', index: 0 }, ctx(p)),
    'd1',
  );
});

test('dimensionField selector with index 2 returns third dimension', () => {
  const p = profile({ traits: traits({ dimensionFields: ['d1', 'd2', 'd3'] }) });
  assertEqual(
    resolveScalarSelector({ source: 'dimensionField', index: 2 }, ctx(p)),
    'd3',
  );
});

test('dimensionField selector index out of bounds → null', () => {
  const p = profile({ traits: traits({ dimensionFields: ['d1'] }) });
  assertEqual(
    resolveScalarSelector({ source: 'dimensionField', index: 5 }, ctx(p)),
    null,
  );
});

test('dimensionField selector empty array → null', () => {
  const p = profile({ traits: traits({ dimensionFields: [] }) });
  assertEqual(
    resolveScalarSelector({ source: 'dimensionField', index: 0 }, ctx(p)),
    null,
  );
});

// ============================================================
// 13. resolveScalarSelector — 列分类字段
// ============================================================

test('temporalField selector with index 0', () => {
  const p = profile({ temporalFields: ['year', 'month'] });
  assertEqual(
    resolveScalarSelector({ source: 'temporalField', index: 0 }, ctx(p)),
    'year',
  );
});

test('temporalField selector index out of bounds → null', () => {
  const p = profile({ temporalFields: [] });
  assertEqual(
    resolveScalarSelector({ source: 'temporalField', index: 0 }, ctx(p)),
    null,
  );
});

test('measureField selector with index 1', () => {
  const p = profile({ measureFields: ['sales', 'profit', 'count'] });
  assertEqual(
    resolveScalarSelector({ source: 'measureField', index: 1 }, ctx(p)),
    'profit',
  );
});

test('measureField selector index out of bounds → null', () => {
  const p = profile({ measureFields: ['sales'] });
  assertEqual(
    resolveScalarSelector({ source: 'measureField', index: 5 }, ctx(p)),
    null,
  );
});

// ============================================================
// 14. resolveScalarSelector — additive / non-additive 过滤
// ============================================================

test('additiveMeasureField picks only additive measures', () => {
  const p = profile({
    measureFields: ['sales', 'avg_price', 'total_count'],
    traits: traits({
      measureKinds: {
        sales: 'additive',
        avg_price: 'non_additive',
        total_count: 'additive',
      },
    }),
  });
  assertEqual(
    resolveScalarSelector({ source: 'additiveMeasureField', index: 0 }, ctx(p)),
    'sales',
  );
  assertEqual(
    resolveScalarSelector({ source: 'additiveMeasureField', index: 1 }, ctx(p)),
    'total_count',
  );
});

test('additiveMeasureField index out of bounds → null', () => {
  const p = profile({
    measureFields: ['sales'],
    traits: traits({ measureKinds: { sales: 'additive' } }),
  });
  assertEqual(
    resolveScalarSelector({ source: 'additiveMeasureField', index: 1 }, ctx(p)),
    null,
  );
});

test('additiveMeasureField no additive measures → null', () => {
  const p = profile({
    measureFields: ['avg_price', 'ratio'],
    traits: traits({
      measureKinds: { avg_price: 'non_additive', ratio: 'non_additive' },
    }),
  });
  assertEqual(
    resolveScalarSelector({ source: 'additiveMeasureField', index: 0 }, ctx(p)),
    null,
  );
});

test('nonAdditiveMeasureField picks only non-additive measures', () => {
  const p = profile({
    measureFields: ['sales', 'avg_price', 'ratio'],
    traits: traits({
      measureKinds: {
        sales: 'additive',
        avg_price: 'non_additive',
        ratio: 'non_additive',
      },
    }),
  });
  assertEqual(
    resolveScalarSelector({ source: 'nonAdditiveMeasureField', index: 0 }, ctx(p)),
    'avg_price',
  );
  assertEqual(
    resolveScalarSelector({ source: 'nonAdditiveMeasureField', index: 1 }, ctx(p)),
    'ratio',
  );
});

test('nonAdditiveMeasureField no non-additive → null', () => {
  const p = profile({
    measureFields: ['sales', 'count'],
    traits: traits({ measureKinds: { sales: 'additive', count: 'additive' } }),
  });
  assertEqual(
    resolveScalarSelector({ source: 'nonAdditiveMeasureField', index: 0 }, ctx(p)),
    null,
  );
});

// ============================================================
// 15. resolveScalarSelector — numericField + exclude
// ============================================================

test('numericField returns first numeric field', () => {
  const p = profile({ numericFields: ['sales', 'profit', 'count'] });
  assertEqual(
    resolveScalarSelector({ source: 'numericField' }, ctx(p)),
    'sales',
  );
});

test('numericField exclude filters out fields', () => {
  const p = profile({ numericFields: ['sales', 'profit', 'count'] });
  assertEqual(
    resolveScalarSelector({ source: 'numericField', exclude: ['sales'] }, ctx(p)),
    'profit',
  );
});

test('numericField exclude all → null', () => {
  const p = profile({ numericFields: ['sales', 'profit'] });
  assertEqual(
    resolveScalarSelector({ source: 'numericField', exclude: ['sales', 'profit'] }, ctx(p)),
    null,
  );
});

test('numericField empty array → null', () => {
  const p = profile({ numericFields: [] });
  assertEqual(
    resolveScalarSelector({ source: 'numericField' }, ctx(p)),
    null,
  );
});

// ============================================================
// 16. resolveScalarSelector — preferredSpec
// ============================================================

test('preferredXField returns spec xField when in columns', () => {
  const spec: ChartSpec = { type: 'bar', xField: 'category' };
  const p = profile({ columns: ['category', 'value'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredXField' }, ctx(p, spec)),
    'category',
  );
});

test('preferredXField returns null when no preferredSpec', () => {
  const p = profile({});
  assertEqual(
    resolveScalarSelector({ source: 'preferredXField' }, ctx(p)),
    null,
  );
});

test('preferredYField with index 1 returns second yField', () => {
  const spec: ChartSpec = { type: 'line', yFields: ['sales', 'profit'] };
  const p = profile({ columns: ['sales', 'profit'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredYField', index: 1 }, ctx(p, spec)),
    'profit',
  );
});

test('preferredYField index out of bounds → null', () => {
  const spec: ChartSpec = { type: 'line', yFields: ['sales'] };
  const p = profile({});
  assertEqual(
    resolveScalarSelector({ source: 'preferredYField', index: 5 }, ctx(p, spec)),
    null,
  );
});

test('preferredSeriesField returns spec seriesField when in columns', () => {
  const spec: ChartSpec = { type: 'line', seriesField: 'region' };
  const p = profile({ columns: ['region', 'year'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredSeriesField' }, ctx(p, spec)),
    'region',
  );
});

test('preferredSizeField returns spec sizeField when in columns', () => {
  const spec: ChartSpec = { type: 'scatter', sizeField: 'revenue' };
  const p = profile({ columns: ['revenue', 'x', 'y'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredSizeField' }, ctx(p, spec)),
    'revenue',
  );
});

test('preferredValueField returns spec valueField when in columns', () => {
  const spec: ChartSpec = { type: 'boxplot', valueField: 'temperature' };
  const p = profile({ columns: ['temperature', 'city'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredValueField' }, ctx(p, spec)),
    'temperature',
  );
});

// ============================================================
// 16b. resolveScalarSelector — preferred field 不在 columns
// ============================================================

test('preferredXField returns null when not in columns', () => {
  const spec: ChartSpec = { type: 'bar', xField: 'missing' };
  const p = profile({ columns: ['category', 'value'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredXField' }, ctx(p, spec)),
    null,
  );
});

test('preferredYField returns null when field not in columns', () => {
  const spec: ChartSpec = { type: 'line', yFields: ['missing', 'profit'] };
  const p = profile({ columns: ['sales', 'profit'] });
  // index 0 → 'missing' 不在 columns
  assertEqual(
    resolveScalarSelector({ source: 'preferredYField', index: 0 }, ctx(p, spec)),
    null,
  );
});

test('preferredSeriesField returns null when not in columns', () => {
  const spec: ChartSpec = { type: 'line', seriesField: 'unknown' };
  const p = profile({ columns: ['year', 'sales'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredSeriesField' }, ctx(p, spec)),
    null,
  );
});

test('preferredXField returns null for empty string', () => {
  const spec: ChartSpec = { type: 'bar', xField: '' };
  const p = profile({ columns: ['', 'value'] });
  assertEqual(
    resolveScalarSelector({ source: 'preferredXField' }, ctx(p, spec)),
    null,
  );
});

// ============================================================
// 17. resolveMultiSelector — 基础
// ============================================================

test('measureFields returns all measures', () => {
  const p = profile({ measureFields: ['sales', 'profit'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFields' }, ctx(p)),
    ['sales', 'profit'],
  );
});

test('measureFields with maxCount truncates', () => {
  const p = profile({ measureFields: ['sales', 'profit', 'count'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFields', maxCount: 2 }, ctx(p)),
    ['sales', 'profit'],
  );
});

test('measureFieldsAfter afterIndex 0 skips first measure', () => {
  const p = profile({ measureFields: ['sales', 'profit', 'count'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFieldsAfter', afterIndex: 0 }, ctx(p)),
    ['profit', 'count'],
  );
});

test('measureFieldsAfter afterIndex 0 with maxCount 1 returns second measure only', () => {
  const p = profile({ measureFields: ['sales', 'profit', 'count'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFieldsAfter', afterIndex: 0, maxCount: 1 }, ctx(p)),
    ['profit'],
  );
});

test('measureFieldsAfter afterIndex 1 skips first two measures', () => {
  const p = profile({ measureFields: ['a', 'b', 'c', 'd'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFieldsAfter', afterIndex: 1 }, ctx(p)),
    ['c', 'd'],
  );
});

test('measureFieldsAfter insufficient remaining → empty array', () => {
  // 仅 1 个 measure，afterIndex:0 要求跳过首个 → 无剩余
  const p = profile({ measureFields: ['sales'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFieldsAfter', afterIndex: 0, maxCount: 1 }, ctx(p)),
    [],
  );
});

test('measureFieldsAfter afterIndex out of bounds → empty array', () => {
  const p = profile({ measureFields: ['sales', 'profit'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'measureFieldsAfter', afterIndex: 5, maxCount: 1 }, ctx(p)),
    [],
  );
});

test('dimensionFields returns all dimensions', () => {
  const p = profile({ traits: traits({ dimensionFields: ['d1', 'd2'] }) });
  assertDeepEqual(
    resolveMultiSelector({ source: 'dimensionFields' }, ctx(p)),
    ['d1', 'd2'],
  );
});

test('temporalFields returns all temporal fields', () => {
  const p = profile({ temporalFields: ['year', 'month', 'day'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'temporalFields' }, ctx(p)),
    ['year', 'month', 'day'],
  );
});

test('temporalFields with maxCount 1 returns first only', () => {
  const p = profile({ temporalFields: ['year', 'month'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'temporalFields', maxCount: 1 }, ctx(p)),
    ['year'],
  );
});

// ============================================================
// 18. resolveMultiSelector — additive / non-additive 过滤
// ============================================================

test('additiveMeasureFields filters by measureKind', () => {
  const p = profile({
    measureFields: ['sales', 'avg_price', 'count', 'ratio'],
    traits: traits({
      measureKinds: {
        sales: 'additive',
        avg_price: 'non_additive',
        count: 'additive',
        ratio: 'non_additive',
      },
    }),
  });
  assertDeepEqual(
    resolveMultiSelector({ source: 'additiveMeasureFields' }, ctx(p)),
    ['sales', 'count'],
  );
});

test('additiveMeasureFields with maxCount limits results', () => {
  const p = profile({
    measureFields: ['sales', 'count', 'total'],
    traits: traits({
      measureKinds: { sales: 'additive', count: 'additive', total: 'additive' },
    }),
  });
  assertDeepEqual(
    resolveMultiSelector({ source: 'additiveMeasureFields', maxCount: 2 }, ctx(p)),
    ['sales', 'count'],
  );
});

test('nonAdditiveMeasureFields filters by measureKind', () => {
  const p = profile({
    measureFields: ['sales', 'avg_price', 'ratio'],
    traits: traits({
      measureKinds: { sales: 'additive', avg_price: 'non_additive', ratio: 'non_additive' },
    }),
  });
  assertDeepEqual(
    resolveMultiSelector({ source: 'nonAdditiveMeasureFields' }, ctx(p)),
    ['avg_price', 'ratio'],
  );
});

// ============================================================
// 19. resolveMultiSelector — preferredSpec
// ============================================================

test('preferredYFields returns spec yFields filtered to columns', () => {
  const spec: ChartSpec = { type: 'bar', yFields: ['sales', 'profit'] };
  const p = profile({ columns: ['sales', 'profit', 'year'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'preferredYFields' }, ctx(p, spec)),
    ['sales', 'profit'],
  );
});

test('preferredYFields returns empty array when no preferredSpec', () => {
  const p = profile({});
  assertDeepEqual(
    resolveMultiSelector({ source: 'preferredYFields' }, ctx(p)),
    [],
  );
});

test('preferredYFields filters: mixed valid and invalid fields', () => {
  const spec: ChartSpec = { type: 'bar', yFields: ['sales', 'missing', 'profit', 'also_missing'] };
  const p = profile({ columns: ['sales', 'profit', 'year'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'preferredYFields' }, ctx(p, spec)),
    ['sales', 'profit'],
  );
});

test('preferredYFields all invalid → empty array', () => {
  const spec: ChartSpec = { type: 'bar', yFields: ['a', 'b'] };
  const p = profile({ columns: ['sales', 'profit'] });
  assertDeepEqual(
    resolveMultiSelector({ source: 'preferredYFields' }, ctx(p, spec)),
    [],
  );
});

test('preferredYFields returns new array (not spec array reference)', () => {
  const yFields: string[] = ['sales', 'profit'];
  const spec: ChartSpec = { type: 'bar', yFields };
  const p = profile({ columns: ['sales', 'profit'] });
  const result = resolveMultiSelector({ source: 'preferredYFields' }, ctx(p, spec));
  // 值相同
  assertDeepEqual(result, ['sales', 'profit']);
  // 但不是同一个数组引用
  assertOk(result !== yFields, 'should return new array, not spec.yFields reference');
});

// ============================================================
// 20. Resolver 不修改输入
// ============================================================

test('resolveScalarSelector does not mutate profile', () => {
  const p = profile({
    measureFields: ['sales'],
    numericFields: ['sales', 'year'],
    traits: traits({ measureKinds: { sales: 'additive' } }),
  });
  const snap = JSON.stringify(p);
  resolveScalarSelector({ source: 'additiveMeasureField', index: 0 }, ctx(p));
  resolveScalarSelector({ source: 'numericField', exclude: ['year'] }, ctx(p));
  assertEqual(JSON.stringify(p), snap, 'profile should not be mutated');
});

test('resolveScalarSelector does not mutate preferredSpec', () => {
  const spec: ChartSpec = { type: 'bar', yFields: ['sales'] };
  const snap = JSON.stringify(spec);
  resolveScalarSelector({ source: 'preferredYField', index: 0 }, ctx(profile({}), spec));
  assertEqual(JSON.stringify(spec), snap, 'spec should not be mutated');
});

test('resolveMultiSelector does not mutate profile', () => {
  const p = profile({
    measureFields: ['sales', 'avg_price'],
    traits: traits({
      measureKinds: { sales: 'additive', avg_price: 'non_additive' },
    }),
  });
  const snap = JSON.stringify(p);
  resolveMultiSelector({ source: 'additiveMeasureFields' }, ctx(p));
  assertEqual(JSON.stringify(p), snap, 'profile should not be mutated');
});

// ============================================================
// 21. 边界情况：trait 值非 boolean 也非 number
// ============================================================
// （理论上不会发生，因为 TraitRequirement 的 trait 字段被类型限定为 BooleanTraitName | NumericTraitName，
//  这些名称对应的 DatasetTraitsV2 值都是 boolean 或 number。但运行时防御性检查。）

test('matchTraitRequirement returns false for unexpected value type', () => {
  // 构造一个运行时非法的 traits 对象（通过类型断言绕过编译检查）
  const badTraits = { measureCount: 'not_a_number' } as unknown as DatasetTraitsV2;
  assertOk(!matchTraitRequirement(
    { trait: 'measureCount', equals: 1 },
    badTraits,
  ));
});

// ============================================================
// 22. 编译期 @ts-expect-error 测试
// ============================================================

// 1) boolean trait 不能使用 min
// @ts-expect-error: boolean trait must not use min
const _check1: TraitRequirement = { trait: 'hasNegativeValues', min: 0 };

// 2) numeric trait 不能使用 required
// @ts-expect-error: numeric trait must not use required
const _check2: TraitRequirement = { trait: 'measureCount', required: true };

// 3) 不能同时设置 required 和 forbidden
// @ts-expect-error: cannot combine required and forbidden
const _check3: TraitRequirement = { trait: 'duplicateDimensionKeys', required: true, forbidden: true };

void _check1; void _check2; void _check3;

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 ChartCapability Resolver Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
