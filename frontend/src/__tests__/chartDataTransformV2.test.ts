// chartDataTransformV2.test.ts — V2 DataTransform 契约测试（pilot: none / group_by_sum / boxplot_summary）
//
// 纯函数测试：覆盖正常转换、错误码、输入不可变。

import {
  executeDataTransformV2,
  type DataTransformInputV2,
} from '../chartDataTransformV2.js';
import type { Row } from '../datasetProfilerV2.js';
import type { ChartSpec } from '../types.js';

let passed = 0;
let failed = 0;

// ---- 内联断言 ----

function assertEqual<T>(actual: T, expected: T, msg?: string): void {
  if (actual !== expected) {
    throw new Error(msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertDeepEqual(actual: unknown, expected: unknown, msg?: string): void {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) {
    throw new Error(msg ?? `expected ${b}, got ${a}`);
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

// ---- 辅助数据 ----

const BASIC_SPEC: ChartSpec = { type: 'bar' };

function snap(input: DataTransformInputV2): string {
  return JSON.stringify({ columns: input.columns, rows: input.rows, spec: input.spec });
}

// ============================================================
// 1. none：深拷贝且不修改输入
// ============================================================

test('none: returns deep copy with same data', () => {
  const input: DataTransformInputV2 = {
    columns: ['product', 'sales'],
    rows: [{ product: 'A', sales: 100 }],
    spec: BASIC_SPEC,
    transform: 'none',
  };
  const snapBefore = snap(input);

  const result = executeDataTransformV2(input);

  assertOk(result.ok, 'should succeed');
  assertEqual(result.errorCode, null);
  assertDeepEqual(result.columns, ['product', 'sales']);
  assertDeepEqual(result.rows, [{ product: 'A', sales: 100 }]);
  assertEqual(result.spec.type, 'bar');

  // 验证深拷贝：修改 result 不影响 input
  (result.rows[0] as Record<string, unknown>).sales = 999;
  assertEqual(input.rows[0].sales, 100, 'input rows should not be mutated');
  assertEqual(snap(input), snapBefore, 'input should be unchanged');
});

test('none: handles empty rows', () => {
  const input: DataTransformInputV2 = {
    columns: ['a'],
    rows: [],
    spec: BASIC_SPEC,
    transform: 'none',
  };
  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 0);
});

// ============================================================
// 2. group_by_sum：基本分组求和
// ============================================================

test('group_by_sum: single group, single yField', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: 100 },
      { city: '北京', sales: 200 },
    ],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertDeepEqual(result.columns, ['city', 'sales']);
  assertEqual(result.rows.length, 1);
  assertEqual(result.rows[0].city, '北京');
  assertEqual(result.rows[0].sales, 300);
  assertEqual(result.spec.xField, 'city');
  assertDeepEqual(result.spec.yFields, ['sales']);
});

test('group_by_sum: multiple groups', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: 100 },
      { city: '上海', sales: 50 },
      { city: '北京', sales: 200 },
      { city: '上海', sales: 150 },
    ],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 2);

  const beijing = result.rows.find(r => r.city === '北京')!;
  const shanghai = result.rows.find(r => r.city === '上海')!;
  assertEqual(beijing.sales, 300);
  assertEqual(shanghai.sales, 200);
});

test('group_by_sum: multiple yFields', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales', 'profit'],
    rows: [
      { city: '北京', sales: 100, profit: 10 },
      { city: '北京', sales: 200, profit: 30 },
    ],
    spec: { type: 'bar', xField: 'city', yFields: ['sales', 'profit'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertDeepEqual(result.columns, ['city', 'sales', 'profit']);
  assertEqual(result.rows.length, 1);
  assertEqual(result.rows[0].sales, 300);
  assertEqual(result.rows[0].profit, 40);
});

test('group_by_sum: skips null values', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: 100 },
      { city: '北京', sales: null },
    ] as Row[],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows[0].sales, 100);
});

test('group_by_sum: converts numeric strings', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: '100' },
      { city: '北京', sales: 200 },
    ] as Row[],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows[0].sales, 300);
});

test('group_by_sum: skips non-numeric values', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: 100 },
      { city: '北京', sales: 'N/A' },
    ] as Row[],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows[0].sales, 100);
});

test('group_by_sum: error when xField missing', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_xField');
});

test('group_by_sum: error when xField not in columns', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'region', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_xField');
});

test('group_by_sum: error when yFields missing', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'city' },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_yFields');
});

test('group_by_sum: error when no yField in columns', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['revenue'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_valid_yFields');
});

test('group_by_sum: error on empty rows', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_valid_data');
});

test('group_by_sum: does not mutate input', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };
  const snapBefore = snap(input);

  executeDataTransformV2(input);

  assertEqual(snap(input), snapBefore, 'input should be unchanged');
});

// ============================================================
// 3. boxplot_summary：分位数算法
// ============================================================

test('boxplot: odd sample count (5 values)', () => {
  // [1, 2, 3, 4, 5] → median=3, Q1=1.5, Q3=4.5
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 1 },
      { group: 'A', score: 2 },
      { group: 'A', score: 3 },
      { group: 'A', score: 4 },
      { group: 'A', score: 5 },
    ],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok, result.errorCode ?? 'should succeed');
  assertDeepEqual(result.columns, ['group', 'min', 'q1', 'median', 'q3', 'max']);
  const row = result.rows[0];
  assertEqual(row.min, 1);
  assertEqual(row.q1, 1.5);
  assertEqual(row.median, 3);
  assertEqual(row.q3, 4.5);
  assertEqual(row.max, 5);
});

test('boxplot: even sample count (4 values)', () => {
  // [1, 2, 3, 4] → median=2.5, Q1=1.5, Q3=3.5
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 1 },
      { group: 'A', score: 2 },
      { group: 'A', score: 3 },
      { group: 'A', score: 4 },
    ],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok, result.errorCode ?? 'should succeed');
  assertEqual(result.rows[0].min, 1);
  assertEqual(result.rows[0].q1, 1.5);
  assertEqual(result.rows[0].median, 2.5);
  assertEqual(result.rows[0].q3, 3.5);
  assertEqual(result.rows[0].max, 4);
});

test('boxplot: even sample count (6 values)', () => {
  // [1, 2, 3, 4, 5, 6] → median=3.5, Q1=2, Q3=5
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 5 },
      { group: 'A', score: 1 },
      { group: 'A', score: 3 },
      { group: 'A', score: 6 },
      { group: 'A', score: 2 },
      { group: 'A', score: 4 },
    ],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok, result.errorCode ?? 'should succeed');
  assertEqual(result.rows[0].min, 1);
  assertEqual(result.rows[0].q1, 2);
  assertEqual(result.rows[0].median, 3.5);
  assertEqual(result.rows[0].q3, 5);
  assertEqual(result.rows[0].max, 6);
});

test('boxplot: multiple groups', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'price'],
    rows: [
      { city: '北京', price: 100 },
      { city: '北京', price: 200 },
      { city: '北京', price: 300 },
      { city: '北京', price: 400 },
      { city: '上海', price: 50 },
      { city: '上海', price: 60 },
      { city: '上海', price: 70 },
      { city: '上海', price: 80 },
    ],
    spec: { type: 'boxplot', xField: 'city', valueField: 'price' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 2);

  const bj = result.rows.find(r => r.city === '北京')!;
  assertEqual(bj.min, 100);
  assertEqual(bj.max, 400);

  const sh = result.rows.find(r => r.city === '上海')!;
  assertEqual(sh.min, 50);
  assertEqual(sh.max, 80);
});

test('boxplot: skips null and non-numeric values', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 10 },
      { group: 'A', score: null },
      { group: 'A', score: 'N/A' },
      { group: 'A', score: 20 },
      { group: 'A', score: 30 },
    ] as Row[],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  // 有效值: [10, 20, 30] → 3 个，足够
  assertOk(result.ok, result.errorCode ?? 'should succeed');
  assertEqual(result.rows[0].min, 10);
  assertEqual(result.rows[0].max, 30);
});

test('boxplot: converts numeric strings', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: '10' },
      { group: 'A', score: '20' },
      { group: 'A', score: '30' },
    ] as Row[],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows[0].min, 10);
  assertEqual(result.rows[0].max, 30);
});

test('boxplot: error when less than 2 valid samples in a group', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 10 },
      { group: 'A', score: null },
    ] as Row[],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertOk(result.errorCode!.startsWith('insufficient_samples_'), result.errorCode!);
});

test('boxplot: error when xField missing', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [{ group: 'A', score: 10 }, { group: 'A', score: 20 }],
    spec: { type: 'boxplot', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_xField');
});

test('boxplot: error when valueField missing', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [{ group: 'A', score: 10 }, { group: 'A', score: 20 }],
    spec: { type: 'boxplot', xField: 'group' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_valueField');
});

test('boxplot: error on empty rows', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_valid_data');
});

test('boxplot: does not mutate input', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 1 },
      { group: 'A', score: 2 },
      { group: 'A', score: 3 },
    ],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };
  const snapBefore = snap(input);

  executeDataTransformV2(input);

  assertEqual(snap(input), snapBefore, 'input should be unchanged');
});

test('boxplot: output spec matches output columns', () => {
  const input: DataTransformInputV2 = {
    columns: ['group', 'score'],
    rows: [
      { group: 'A', score: 1 },
      { group: 'A', score: 2 },
      { group: 'A', score: 3 },
    ],
    spec: { type: 'boxplot', xField: 'group', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  // spec 中 valueField 应被移除，yFields 为五数概括
  assertEqual(result.spec.valueField, undefined);
  assertDeepEqual(result.spec.yFields, ['min', 'q1', 'median', 'q3', 'max']);
  assertEqual(result.spec.xField, 'group');
  assertEqual(result.spec.type, 'boxplot');
});

// ============================================================
// 4. 未实现 transform → error
// ============================================================

test('unsupported transform returns error', () => {
  const input: DataTransformInputV2 = {
    columns: ['a'],
    rows: [{ a: 1 }],
    spec: BASIC_SPEC,
    transform: 'group_by_average' as DataTransformInputV2['transform'],
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'unsupported_transform_group_by_average');
});

// ============================================================
// 5. spec 引用隔离：修改结果不影响输入
// ============================================================

test('spec isolation: mutating result spec.yFields does not affect input (success)', () => {
  const yFields = ['sales'];
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'city', yFields },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  // 修改结果 spec.yFields
  result.spec.yFields!.push('profit');
  // 输入 spec.yFields 不受影响
  assertEqual(input.spec.yFields!.length, 1);
  assertEqual(input.spec.yFields![0], 'sales');
});

test('spec isolation: failure result does not share yFields with input', () => {
  const yFields = ['sales'];
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [],
    spec: { type: 'bar', xField: 'city', yFields },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  // 失败结果也有自己的 spec，修改不影响输入
  result.spec.yFields = ['modified'];
  assertEqual(input.spec.yFields!.length, 1);
  assertEqual(input.spec.yFields![0], 'sales');
});

// ============================================================
// 6. group_by_sum：去重与边界
// ============================================================

test('group_by_sum: duplicate yFields are deduplicated and not double-summed', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [{ city: '北京', sales: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['sales', 'sales', 'sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  // 去重后 columns 仅一个 sales
  assertDeepEqual(result.columns, ['city', 'sales']);
  // 求和结果不应翻倍
  assertEqual(result.rows[0].sales, 100);
});

test('group_by_sum: all-invalid rows do not produce zero-value row', () => {
  // 北京行全部为 null / 非数值 → 不应输出北京分组
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '北京', sales: null },
      { city: '北京', sales: 'N/A' },
      { city: '上海', sales: 50 },
    ] as Row[],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 1, 'only Shanghai should be output');
  assertEqual(result.rows[0].city, '上海');
  assertEqual(result.rows[0].sales, 50);
});

test('group_by_sum: null xField rows are skipped', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: null, sales: 100 },
      { city: '上海', sales: 50 },
    ] as Row[],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 1, 'null xField should be skipped');
  assertEqual(result.rows[0].city, '上海');
});

test('group_by_sum: empty string xField rows are skipped', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'sales'],
    rows: [
      { city: '', sales: 100 },
      { city: '上海', sales: 50 },
    ],
    spec: { type: 'bar', xField: 'city', yFields: ['sales'] },
    transform: 'group_by_sum',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 1, 'empty string xField should be skipped');
  assertEqual(result.rows[0].city, '上海');
});

// ============================================================
// 7. boxplot_summary：xField 冲突
// ============================================================

test('boxplot: xField conflicts with stat fields → error', () => {
  // xField='median' 与输出统计字段冲突
  const input: DataTransformInputV2 = {
    columns: ['median', 'score'],
    rows: [
      { median: 'A', score: 10 },
      { median: 'A', score: 20 },
      { median: 'A', score: 30 },
    ],
    spec: { type: 'boxplot', xField: 'median', valueField: 'score' },
    transform: 'boxplot_summary',
  };

  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'xField_conflicts_with_stat_fields');
});

// ============================================================
// 8. matrix_aggregate：按 (xField, yField) SUM 聚合
// ============================================================

test('matrix_aggregate: no duplicates → output unchanged', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'avg_temp'],
    rows: [
      { region: '城北', month: '1月', avg_temp: 5.2 },
      { region: '城北', month: '2月', avg_temp: 7.1 },
      { region: '城南', month: '1月', avg_temp: 6.0 },
    ],
    spec: { type: 'heatmap', xField: 'region', yFields: ['month'], valueField: 'avg_temp' },
    transform: 'matrix_aggregate',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok, result.errorCode ?? 'should succeed');
  assertDeepEqual(result.columns, ['region', 'month', 'avg_temp']);
  assertEqual(result.rows.length, 3);
  assertEqual(result.spec.type, 'heatmap');
});

test('matrix_aggregate: duplicate (xField, yField) → SUM aggregation', () => {
  const input: DataTransformInputV2 = {
    columns: ['city', 'product', 'sales'],
    rows: [
      { city: '北京', product: 'A', sales: 100 },
      { city: '北京', product: 'A', sales: 200 },
      { city: '北京', product: 'B', sales: 50 },
      { city: '上海', product: 'A', sales: 80 },
    ],
    spec: { type: 'heatmap', xField: 'city', yFields: ['product'], valueField: 'sales' },
    transform: 'matrix_aggregate',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 3, '3 unique (city, product) pairs');
  // 北京+A → 300
  const ba = result.rows.find(r => r.city === '北京' && r.product === 'A')!;
  assertOk(ba !== undefined, '北京+A should exist');
  assertEqual(ba.sales, 300);
  // 北京+B → 50
  const bb = result.rows.find(r => r.city === '北京' && r.product === 'B')!;
  assertEqual(bb.sales, 50);
  // 上海+A → 80
  const sa = result.rows.find(r => r.city === '上海' && r.product === 'A')!;
  assertEqual(sa.sales, 80);
});

test('matrix_aggregate: sparse matrix → missing cells not output', () => {
  // 3×3 matrix, only 5 cells have data
  const input: DataTransformInputV2 = {
    columns: ['x', 'y', 'v'],
    rows: [
      { x: 'A', y: '1', v: 10 },
      { x: 'A', y: '2', v: 20 },
      { x: 'B', y: '2', v: 30 },
      { x: 'B', y: '3', v: 40 },
      { x: 'C', y: '1', v: 50 },
    ],
    spec: { type: 'heatmap', xField: 'x', yFields: ['y'], valueField: 'v' },
    transform: 'matrix_aggregate',
  };

  const result = executeDataTransformV2(input);
  assertOk(result.ok);
  assertEqual(result.rows.length, 5, 'only 5 cells output, missing cells not padded');
  // verify no zero-value rows for missing cells
  const cells = new Set(result.rows.map(r => `${r.x}|${r.y}`));
  assertEqual(cells.size, 5);
});

test('matrix_aggregate: missing xField → error', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'val'],
    rows: [{ region: 'A', month: '1', val: 10 }],
    spec: { type: 'heatmap', yFields: ['month'], valueField: 'val' },
    transform: 'matrix_aggregate',
  };
  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_xField');
});

test('matrix_aggregate: missing yField → error', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'val'],
    rows: [{ region: 'A', month: '1', val: 10 }],
    spec: { type: 'heatmap', xField: 'region', valueField: 'val' },
    transform: 'matrix_aggregate',
  };
  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_yField');
});

test('matrix_aggregate: missing valueField → error', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'val'],
    rows: [{ region: 'A', month: '1', val: 10 }],
    spec: { type: 'heatmap', xField: 'region', yFields: ['month'] },
    transform: 'matrix_aggregate',
  };
  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'missing_valueField');
});

test('matrix_aggregate: all null / non-numeric values → no_valid_data', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'val'],
    rows: [
      { region: 'A', month: '1', val: null },
      { region: 'B', month: '2', val: 'N/A' },
    ] as Row[],
    spec: { type: 'heatmap', xField: 'region', yFields: ['month'], valueField: 'val' },
    transform: 'matrix_aggregate',
  };
  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'no_valid_data');
});

test('matrix_aggregate: does not mutate input', () => {
  const input: DataTransformInputV2 = {
    columns: ['region', 'month', 'val'],
    rows: [
      { region: 'A', month: '1', val: 10 },
      { region: 'A', month: '1', val: 20 },
    ],
    spec: { type: 'heatmap', xField: 'region', yFields: ['month'], valueField: 'val' },
    transform: 'matrix_aggregate',
  };
  const snapBefore = snap(input);
  executeDataTransformV2(input);
  assertEqual(snap(input), snapBefore, 'input should be unchanged');
});

test('matrix_aggregate: xField equals yField → error', () => {
  const input: DataTransformInputV2 = {
    columns: ['dim', 'val'],
    rows: [{ dim: 'A', val: 10 }],
    spec: { type: 'heatmap', xField: 'dim', yFields: ['dim'], valueField: 'val' },
    transform: 'matrix_aggregate',
  };
  const result = executeDataTransformV2(input);
  assertEqual(result.ok, false);
  assertEqual(result.errorCode, 'field_conflict');
});

// ============================================================
// 结果汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`V2 DataTransform Pilot Tests`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
