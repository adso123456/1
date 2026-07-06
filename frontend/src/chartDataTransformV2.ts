// chartDataTransformV2.ts — V2 数据转换执行（pilot: none / group_by_sum / boxplot_summary）
//
// 阶段 E：纯函数，不接入运行时，不修改输入的 rows/columns/spec。
// 仅实现 Planner 已引用的 transform，不覆盖其他 DataTransformPlan 值。

import type { Row } from './datasetProfilerV2.js';
import type { ChartSpec } from './types.js';
import type { DataTransformPlan } from './chartCapabilityV2.js';
import { toNumber } from './chartSemantics.js';

// ============================================================
// 导出类型
// ============================================================

/** 数据转换输入 */
export interface DataTransformInputV2 {
  columns: string[];
  rows: Row[];
  spec: ChartSpec;
  transform: DataTransformPlan;
}

/** 数据转换结果 */
export interface DataTransformResultV2 {
  ok: boolean;
  columns: string[];
  rows: Row[];
  /** 转换后的 spec，与输出字段匹配 */
  spec: ChartSpec;
  /** 失败时非 null */
  errorCode: string | null;
}

// ============================================================
// 内部工具
// ============================================================

/**
 * 克隆 ChartSpec，确保 yFields 为新数组。
 * 成功和失败结果均通过此函数生成 spec，不得复用输入 spec 的数组引用。
 */
function cloneSpec(spec: ChartSpec): ChartSpec {
  return {
    ...spec,
    yFields: spec.yFields ? [...spec.yFields] : spec.yFields,
  };
}

// ============================================================
// 主入口
// ============================================================

/**
 * 根据 transform 计划对输入数据执行转换。
 *
 * 转换后的 spec 字段与输出 columns 保持一致：
 * - none: spec 不变
 * - group_by_sum: spec.yFields 更新为实际输出的指标字段
 * - boxplot_summary: spec.valueField 移除，yFields 置为五数概括字段
 *
 * 所有分支均不修改 input 或其嵌套对象。
 */
export function executeDataTransformV2(
  input: DataTransformInputV2,
): DataTransformResultV2 {
  switch (input.transform) {
    case 'none':
      return executeNone(input);
    case 'group_by_sum':
      return executeGroupBySum(input);
    case 'boxplot_summary':
      return executeBoxplotSummary(input);
    case 'matrix_aggregate':
      return executeMatrixAggregate(input);
    default:
      return {
        ok: false,
        columns: [],
        rows: [],
        spec: cloneSpec(input.spec),
        errorCode: `unsupported_transform_${input.transform}`,
      };
  }
}

// ============================================================
// none
// ============================================================

function executeNone(input: DataTransformInputV2): DataTransformResultV2 {
  return {
    ok: true,
    columns: [...input.columns],
    rows: input.rows.map(row => ({ ...row })),
    spec: cloneSpec(input.spec),
    errorCode: null,
  };
}

// ============================================================
// group_by_sum
// ============================================================

function executeGroupBySum(input: DataTransformInputV2): DataTransformResultV2 {
  const { columns, rows, spec } = input;
  const xField = spec.xField;

  // 校验 xField
  if (!xField || !columns.includes(xField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_xField',
    };
  }

  // 校验 yFields
  const yFields = spec.yFields;
  if (!yFields || yFields.length === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_yFields',
    };
  }

  // 去重并保持原顺序，再过滤仅存在于 columns 中的字段
  const seen = new Set<string>();
  const dedupedYFields: string[] = [];
  for (const f of yFields) {
    if (!seen.has(f)) {
      seen.add(f);
      dedupedYFields.push(f);
    }
  }
  const validYFields = dedupedYFields.filter(f => columns.includes(f));
  if (validYFields.length === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'no_valid_yFields',
    };
  }

  // 分组求和（懒初始化：仅当行中有有效数值时才创建分组）
  const groupSums = new Map<string, Record<string, number>>();
  let hasValidData = false;

  for (const row of rows) {
    // 跳过无效 xField（null / undefined / 空字符串）
    const xVal = row[xField];
    if (xVal === null || xVal === undefined || xVal === '') continue;

    const key = String(xVal);

    // 检查此行是否有任何有效数值
    let rowHasValid = false;
    for (const yf of validYFields) {
      if (toNumber(row[yf]) !== null) {
        rowHasValid = true;
        break;
      }
    }
    if (!rowHasValid) continue;

    // 懒初始化分组
    if (!groupSums.has(key)) {
      const init: Record<string, number> = {};
      for (const yf of validYFields) init[yf] = 0;
      groupSums.set(key, init);
    }

    const acc = groupSums.get(key)!;
    for (const yf of validYFields) {
      const n = toNumber(row[yf]);
      if (n !== null) {
        acc[yf] += n;
        hasValidData = true;
      }
    }
  }

  if (!hasValidData || groupSums.size === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'no_valid_data',
    };
  }

  // 构建输出行
  const outputColumns = [xField, ...validYFields];
  const outputRows: Row[] = [];
  for (const [key, sums] of groupSums) {
    const outRow: Row = { [xField]: key };
    for (const yf of validYFields) outRow[yf] = sums[yf];
    outputRows.push(outRow);
  }

  return {
    ok: true,
    columns: outputColumns,
    rows: outputRows,
    spec: { ...cloneSpec(spec), xField, yFields: validYFields },
    errorCode: null,
  };
}

// ============================================================
// boxplot_summary
// ============================================================

/**
 * 计算中位数（假定输入已排序）。
 * 奇数 n → 中间值；偶数 n → 两个中间值的平均。
 */
function median(sorted: number[]): number {
  const n = sorted.length;
  const mid = Math.floor(n / 2);
  if (n % 2 === 1) return sorted[mid];
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

/** 五数概括字段名（不可被 xField 占用） */
const STAT_FIELDS = ['min', 'q1', 'median', 'q3', 'max'] as const;

function executeBoxplotSummary(input: DataTransformInputV2): DataTransformResultV2 {
  const { columns, rows, spec } = input;
  const xField = spec.xField;
  const valueField = spec.valueField;

  // 校验 xField
  if (!xField || !columns.includes(xField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_xField',
    };
  }

  // 校验 valueField
  if (!valueField || !columns.includes(valueField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_valueField',
    };
  }

  // 检查 xField 是否与统计字段名冲突（禁止覆盖分组字段）
  if ((STAT_FIELDS as readonly string[]).includes(xField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'xField_conflicts_with_stat_fields',
    };
  }

  // 分组收集数值（跳过无效 xField）
  const groups = new Map<string, number[]>();
  for (const row of rows) {
    const xVal = row[xField];
    if (xVal === null || xVal === undefined || xVal === '') continue;

    const key = String(xVal);
    const n = toNumber(row[valueField]);
    if (n === null) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(n);
  }

  if (groups.size === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'no_valid_data',
    };
  }

  // 每组至少 2 个有效数值
  for (const [key, vals] of groups) {
    if (vals.length < 2) {
      return {
        ok: false, columns: [], rows: [],
        spec: cloneSpec(spec), errorCode: `insufficient_samples_${key}`,
      };
    }
  }

  // 计算五数概括（exclusive / Tukey's hinges 分位数）
  const statFields = [...STAT_FIELDS];
  const outputColumns = [xField, ...statFields];
  const outputRows: Row[] = [];

  for (const [key, vals] of groups) {
    const sorted = [...vals].sort((a, b) => a - b);
    const n = sorted.length;
    const mid = Math.floor(n / 2);

    const min = sorted[0];
    const max = sorted[n - 1];
    const med = median(sorted);

    // 上下半分割（exclusive：奇数时排除中位数）
    const lowerHalf = n % 2 === 1
      ? sorted.slice(0, mid)
      : sorted.slice(0, mid);
    const upperHalf = n % 2 === 1
      ? sorted.slice(mid + 1)
      : sorted.slice(mid);

    const q1 = median(lowerHalf);
    const q3 = median(upperHalf);

    outputRows.push({
      [xField]: key,
      min,
      q1,
      median: med,
      q3,
      max,
    });
  }

  // spec: 移除 valueField，yFields 置为五数概括字段（新数组）
  const { valueField: _vf, ...restSpec } = cloneSpec(spec);
  const outputSpec: ChartSpec = {
    ...restSpec,
    xField,
    yFields: statFields,
  };

  return {
    ok: true,
    columns: outputColumns,
    rows: outputRows,
    spec: outputSpec,
    errorCode: null,
  };
}

// ============================================================
// matrix_aggregate
// ============================================================

/**
 * 按 (xField, yFields[0]) 分组对 valueField 执行 SUM 聚合。
 * 输出长表同构格式，旧 buildHeatmapChart() 可直接消费。
 * 稀疏单元格不输出行。不修改输入。
 */
function executeMatrixAggregate(input: DataTransformInputV2): DataTransformResultV2 {
  const { columns, rows, spec } = input;
  const xField = spec.xField;
  const yField = spec.yFields?.[0];
  const valueField = spec.valueField;

  // ── 校验 ──
  if (!xField || !columns.includes(xField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_xField',
    };
  }
  if (!yField || !columns.includes(yField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_yField',
    };
  }
  if (!valueField || !columns.includes(valueField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'missing_valueField',
    };
  }
  if (xField === yField || valueField === xField || valueField === yField) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'field_conflict',
    };
  }

  // ── 聚合 ──
  const aggregates = new Map<string, { xVal: string; yVal: string; sum: number }>();
  let hasValidData = false;

  for (const row of rows) {
    const xVal = row[xField];
    const yVal = row[yField];
    if (xVal === null || xVal === undefined || xVal === ''
      || yVal === null || yVal === undefined || yVal === '') continue;

    const n = toNumber(row[valueField]);
    if (n === null) continue;

    const key = `${String(xVal)}||${String(yVal)}`;
    const existing = aggregates.get(key);
    if (existing) {
      existing.sum += n;
    } else {
      aggregates.set(key, { xVal: String(xVal), yVal: String(yVal), sum: n });
    }
    hasValidData = true;
  }

  if (!hasValidData || aggregates.size === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: cloneSpec(spec), errorCode: 'no_valid_data',
    };
  }

  // ── 构建输出（保持长表格式） ──
  const outputColumns = [xField, yField, valueField];
  const outputRows: Row[] = [];
  for (const { xVal, yVal, sum } of aggregates.values()) {
    outputRows.push({ [xField]: xVal, [yField]: yVal, [valueField]: sum });
  }

  return {
    ok: true,
    columns: outputColumns,
    rows: outputRows,
    spec: cloneSpec(spec),
    errorCode: null,
  };
}
