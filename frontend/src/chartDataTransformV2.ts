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
    default:
      return {
        ok: false,
        columns: [],
        rows: [],
        spec: { ...input.spec },
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
    spec: { ...input.spec },
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
      spec: { ...spec }, errorCode: 'missing_xField',
    };
  }

  // 校验 yFields
  const yFields = spec.yFields;
  if (!yFields || yFields.length === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: { ...spec }, errorCode: 'missing_yFields',
    };
  }

  const validYFields = yFields.filter(f => columns.includes(f));
  if (validYFields.length === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: { ...spec }, errorCode: 'no_valid_yFields',
    };
  }

  // 分组求和
  const groupSums = new Map<string, Record<string, number>>();
  let hasValidData = false;

  for (const row of rows) {
    const key = String(row[xField] ?? '');
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
      spec: { ...spec }, errorCode: 'no_valid_data',
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
    spec: { ...spec, xField, yFields: validYFields },
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

function executeBoxplotSummary(input: DataTransformInputV2): DataTransformResultV2 {
  const { columns, rows, spec } = input;
  const xField = spec.xField;
  const valueField = spec.valueField;

  // 校验 xField
  if (!xField || !columns.includes(xField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: { ...spec }, errorCode: 'missing_xField',
    };
  }

  // 校验 valueField
  if (!valueField || !columns.includes(valueField)) {
    return {
      ok: false, columns: [], rows: [],
      spec: { ...spec }, errorCode: 'missing_valueField',
    };
  }

  // 分组收集数值
  const groups = new Map<string, number[]>();
  for (const row of rows) {
    const key = String(row[xField] ?? '');
    const n = toNumber(row[valueField]);
    if (n === null) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(n);
  }

  if (groups.size === 0) {
    return {
      ok: false, columns: [], rows: [],
      spec: { ...spec }, errorCode: 'no_valid_data',
    };
  }

  // 每组至少 2 个有效数值
  for (const [key, vals] of groups) {
    if (vals.length < 2) {
      return {
        ok: false, columns: [], rows: [],
        spec: { ...spec }, errorCode: `insufficient_samples_${key}`,
      };
    }
  }

  // 计算五数概括（exclusive / Tukey's hinges 分位数）
  const statFields = ['min', 'q1', 'median', 'q3', 'max'];
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

  // spec: 移除 valueField，yFields 置为五数概括字段
  const { valueField: _vf, ...restSpec } = spec;
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
