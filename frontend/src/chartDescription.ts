import type { ChartData, RenderableChartType } from './types';
import {
  toNumber as toNum,
  isOrderedField,
  isSummable,
} from './chartSemantics';
import { formatColumnLabel } from './utils/tableFormatting';

export { isSummable };

/* ---- 工具函数 ---- */

function getYFields(spec: ChartData['spec']): string[] {
  return (spec.yFields ?? []).filter((f): f is string => typeof f === 'string' && f.trim().length > 0);
}

function readableField(name: string): string {
  return name.replace(/_(avg|sum|count|min|max|total)$/, '');
}

/** 展示用字段名：优先用完整原字段的精确中文映射；只有完整字段无映射时，
 *  才回退到删除聚合后缀后的字段名再翻译，避免 outlet_count 被误删为 outlet。
 *  readableField 仅用于 isSummable 等语义判断，展示一律走 displayField。 */
function displayField(name: string): string {
  const direct = formatColumnLabel(name);
  if (direct !== name) return direct;
  const readable = readableField(name);
  return readable === name ? direct : formatColumnLabel(readable);
}

function num(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2).replace(/\.?0+$/, '');
}

function pct(part: number, total: number): string {
  if (total <= 0) return '0%';
  return ((part / total) * 100).toFixed(1) + '%';
}

function ratio(a: number, b: number): string | null {
  if (a === 0 || b === 0) return null;
  if (a * b < 0) return null;
  const absA = Math.abs(a);
  const absB = Math.abs(b);
  const r = Math.max(absA, absB) / Math.min(absA, absB);
  if (r < 1.05) return null;
  return r.toFixed(1) + '倍';
}

/* ---- 共享数据模型 ---- */

interface DataModel {
  dim: string;
  metric: string;
  count: number;
  total: number;
  average: number;
  maxItem: { name: string; value: number };
  minItem: { name: string; value: number };
  top3: { name: string; value: number }[];
  summable: boolean;
  hasNegative: boolean;
  maxMinRatio: string | null;
  ordered: boolean;
}

function buildModel(
  rows: Array<Record<string, unknown>>,
  xField: string,
  y1: string,
  allowNegatives: boolean,
): DataModel | null {
  const items: { name: string; value: number }[] = [];
  let hasNegative = false;
  for (const r of rows) {
    const v = toNum(r[y1]);
    if (v === null) continue;
    if (v < 0) hasNegative = true;
    if (!allowNegatives && v < 0) continue;
    items.push({ name: String(r[xField] ?? ''), value: v });
  }
  if (!items.length) return null;

  items.sort((a, b) => b.value - a.value);

  const count = items.length;
  const total = items.reduce((s, i) => s + i.value, 0);
  const average = total / count;
  const maxItem = items[0];
  const minItem = items[items.length - 1];
  const top3 = items.slice(0, Math.min(3, count));
  const maxMinRatio = ratio(maxItem.value, minItem.value);
  const ordered = isOrderedField(rows, xField);
  const summable = isSummable(y1);

  return {
    dim: displayField(xField),
    metric: displayField(y1),
    count,
    total,
    average,
    maxItem,
    minItem,
    top3,
    summable: summable && !hasNegative,  // 含负数时不作为可加指标
    hasNegative,
    maxMinRatio,
    ordered,
  };
}

/* ---- 说明生成 ---- */

function interactionHint(m: { summable: boolean; hasNegative: boolean }): string {
  if (m.summable && !m.hasNegative) {
    return '将鼠标移到图形上，可查看具体数值与占比。';
  }
  return '将鼠标移到图形上，可查看具体数值。';
}

function comboInteractionHint(): string {
  return '将鼠标移到图形上，可查看各系列具体数值。';
}

/** bar / horizontal_bar / pie / donut 共享文案 */
function buildCategoryDescription(m: DataModel, includeProportion: boolean): string | null {
  const metricLabel = m.metric;
  const parts: string[] = [];

  // 首句：数据项数 + 合计（仅可加、非负）或均值
  if (m.summable) {
    parts.push(`共${m.count}个${m.dim}，${metricLabel}合计${num(m.total)}。`);
    parts.push(`${m.maxItem.name}最多，为${num(m.maxItem.value)}，占${pct(m.maxItem.value, m.total)}；${m.minItem.name}最少，为${num(m.minItem.value)}。`);
    if (includeProportion && m.top3.length >= 3) {
      const topNames = m.top3.map(t => t.name).join('、');
      const top3Pct = pct(m.top3.reduce((s, i) => s + i.value, 0), m.total);
      parts.push(`前${m.top3.length}个${m.dim}（${topNames}）合计占${top3Pct}。`);
    }
  } else {
    parts.push(`共${m.count}个${m.dim}，${metricLabel}均值${num(m.average)}。`);
    parts.push(`${m.maxItem.name}最高，为${num(m.maxItem.value)}；${m.minItem.name}最低，为${num(m.minItem.value)}。`);
  }

  // 差异结论：仅可加、非负有倍数
  if (m.summable && m.maxMinRatio) {
    const diff = Number(m.maxMinRatio.replace('倍', ''));
    const degree = diff >= 5 ? '差异较大' : diff >= 2 ? '差异较明显' : '存在一定差异';
    parts.push(`${m.maxItem.name}约为${m.minItem.name}的${m.maxMinRatio}，${m.dim}间${degree}。`);
  }

  return parts.join('');
}

export function generateChartDescription(
  chart: ChartData,
  renderedType: RenderableChartType,
): string | null {
  const { rows, spec } = chart;
  const xField = (typeof spec.xField === 'string' && spec.xField.trim()) ? spec.xField : null;
  const yFields = getYFields(spec);

  if (!xField || !rows.length) return null;

  switch (renderedType) {
    case 'bar':
    case 'horizontal_bar': {
      const y1 = yFields[0] ?? null;
      if (!y1) return null;
      const m = buildModel(rows, xField, y1, true);
      if (!m) return null;
      const body = buildCategoryDescription(m, m.summable);
      return body ? body + interactionHint(m) : null;
    }

    case 'pie':
    case 'donut': {
      const y1 = yFields[0] ?? null;
      if (!y1) return null;
      const m = buildModel(rows, xField, y1, false);
      if (!m) return null;
      // 饼图总是占比场景
      const mProportion = { ...m, summable: true, hasNegative: false };
      const body = buildCategoryDescription(mProportion, true);
      return body ? body + interactionHint(mProportion) : null;
    }

    case 'line':
    case 'area': {
      const y1 = yFields[0] ?? null;
      if (!y1) return null;
      const m = buildModel(rows, xField, y1, true);
      if (!m) return null;

      const parts: string[] = [];

      if (m.summable) {
        parts.push(`共${m.count}个${m.dim}，${m.metric}合计${num(m.total)}，均值${num(m.average)}。`);
      } else {
        parts.push(`共${m.count}个${m.dim}，${m.metric}均值${num(m.average)}。`);
      }
      parts.push(`最高为${m.maxItem.name}（${num(m.maxItem.value)}），最低为${m.minItem.name}（${num(m.minItem.value)}）。`);

      // 首尾变化描述（仅有序 xField 且首尾非零时）
      if (m.ordered && rows.length >= 2) {
        const rawFirst = toNum(rows[0][y1]);
        const rawLast = toNum(rows[rows.length - 1][y1]);
        if (rawFirst !== null && rawLast !== null && rawFirst > 0 && rawLast > 0) {
          const changePct = Math.abs(((rawLast - rawFirst) / rawFirst) * 100).toFixed(1);
          if (rawLast > rawFirst * 1.05) {
            parts.push(`末值（${num(rawLast)}）较初值（${num(rawFirst)}）增加${changePct}%。`);
          } else if (rawLast < rawFirst * 0.95) {
            parts.push(`末值（${num(rawLast)}）较初值（${num(rawFirst)}）减少${changePct}%。`);
          } else {
            parts.push('首尾变化较小。');
          }
        }
      }

      if (m.summable && m.maxMinRatio) {
        parts.push(`${m.maxItem.name}约为${m.minItem.name}的${m.maxMinRatio}。`);
      }

      parts.push(interactionHint(m));
      return parts.join('');
    }

    case 'combo': {
      const y1 = yFields[0] ?? null;
      const y2 = yFields[1] ?? null;
      if (!y1 || !y2) return null;

      const m1 = buildModel(rows, xField, y1, true);
      if (!m1) return null;

      const barLabel = displayField(y1);
      const lineLabel = displayField(y2);

      let maxCat2 = '';
      let maxVal2 = -Infinity;
      for (const r of rows) {
        const v = toNum(r[y2]);
        if (v !== null && v > maxVal2) { maxVal2 = v; maxCat2 = String(r[xField] ?? ''); }
      }

      const parts = [
        `${barLabel}以柱状图、${lineLabel}以折线图展示，共${m1.count}个${m1.dim}。`,
      ];

      if (m1.summable) {
        parts.push(`${barLabel}合计${num(m1.total)}，${m1.maxItem.name}最高（${num(m1.maxItem.value)}）。`);
      } else {
        parts.push(`${barLabel}最高为${m1.maxItem.name}（${num(m1.maxItem.value)}）。`);
      }

      if (maxCat2) {
        parts.push(`${lineLabel}最高为${maxCat2}（${num(maxVal2)}）。`);
      }

      parts.push(comboInteractionHint());
      return parts.join('');
    }

    default:
      return null;
  }
}
