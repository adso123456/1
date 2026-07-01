import type { ChartData, ChartSpec, ChartType, ChartTypeAvailability, RenderableChartType } from './types';
import { isSummable } from './chartDescription';

type Row = Record<string, unknown>;
type EChartsOption = Record<string, unknown>;

export const RENDERABLE_TYPES: RenderableChartType[] = [
  'bar',
  'horizontal_bar',
  'line',
  'area',
  'pie',
  'donut',
  'scatter',
  'bubble',
  'radar',
  'heatmap',
  'boxplot',
  'gauge',
  'combo',
];

export const CHART_TYPE_LABELS: Record<RenderableChartType, string> = {
  bar: '柱状图',
  horizontal_bar: '横向柱状图',
  line: '折线图',
  area: '面积图',
  pie: '饼图',
  donut: '环形图',
  scatter: '散点图',
  bubble: '气泡图',
  radar: '雷达图',
  heatmap: '热力图',
  boxplot: '箱线图',
  gauge: '仪表盘',
  combo: '组合图',
};

const BLUE_PALETTE = [
  '#2563eb', '#3b82f6', '#60a5fa', '#93c5fd',
  '#1d4ed8', '#1e40af', '#7dd3fc', '#38bdf8',
  '#0ea5e9', '#0284c7', '#0369a1', '#075985',
];

export function isNullValue(v: unknown): boolean {
  return v === null || v === undefined || v === '';
}

export function isRenderableChartType(type: ChartType | undefined): type is RenderableChartType {
  return !!type && RENDERABLE_TYPES.includes(type as RenderableChartType);
}

export function normalizeChartSpec(spec: ChartSpec, type?: RenderableChartType): ChartSpec {
  return {
    ...spec,
    type: type ?? spec.type,
  };
}

export function fallbackSpecFromColumns(
  type: 'bar' | 'line' | 'pie',
  columns: string[],
  title = '数据可视化',
): ChartSpec {
  return {
    type,
    title,
    xField: columns[0] ?? null,
    yFields: columns[1] ? [columns[1]] : [],
    seriesField: null,
    sizeField: null,
  };
}

/** 分类字段 + 单个数值字段时的占比条件：分类数≤6、无非负数 */
function isProportionCompatible(chart: ChartData, categoryCount: number): boolean {
  const yField = getYField(chart.spec);
  if (!yField || !isNumericField(chart.rows, yField)) return false;
  if (chart.rows.some(r => (toNumber(r[yField]) ?? 0) < 0)) return false;
  return categoryCount >= 1 && categoryCount <= 6;
}

export function getCompatibleChartTypes(chart: ChartData): RenderableChartType[] {
  const { columns, rows, spec } = chart;
  const xField = getField(spec, 'xField');
  const yField = getYField(spec);
  const yFieldsAll = (spec.yFields ?? []).filter((f): f is string => typeof f === 'string' && f.trim().length > 0);
  const valueField = getField(spec, 'valueField');
  const sizeField = getField(spec, 'sizeField');

  // xField 是否有序（时间/月份/季度/年份等）
  const xOrdered = isOrderedField(rows, xField);

  // 分类相关
  const rawCats = xField
    ? [...new Set(rows.map(r => String(r[xField])).filter(v => v !== ''))]
    : [];
  const categoryCount = rawCats.length;
  const maxCategoryLen = rawCats.length > 0 ? Math.max(...rawCats.map(s => s.length)) : 0;

  // yField 是否为数值列
  const yNumeric = yField ? isNumericField(rows, yField) : false;

  // yFields 中数值列的个数
  const numericYFieldCount = yFieldsAll.filter(f => isNumericField(rows, f)).length;

  // 推荐类型（来自 LLM）
  const recommended: RenderableChartType | null = isRenderableChartType(spec.type) ? spec.type : null;

  // 构建器兼容性验证
  const canBuild = (type: RenderableChartType) =>
    buildChartOption({ ...chart, spec: normalizeChartSpec(spec, type) }) !== null;

  // 收集候选（去重）
  const set = new Set<RenderableChartType>();

  const add = (t: RenderableChartType) => { set.add(t); };

  // ---- 柱状图 / 横向柱状图（基础安全类型） ----
  if (yNumeric && xField && hasColumn(columns, xField) && hasColumn(columns, yField)) {
    add('bar');
    add('horizontal_bar');
  }

  // ---- 折线图 / 面积图（仅有序 xField） ----
  if (xOrdered && yNumeric && canBuild('line')) {
    add('line');
    add('area');
  }

  // ---- 饼图 / 环形图 ----
  // 前端无法判断“是否属于整体”，只在 LLM 推荐饼图/环形图时提供候选
  if (
    (recommended === 'pie' || recommended === 'donut') &&
    yNumeric &&
    numericYFieldCount === 1 &&
    isProportionCompatible(chart, categoryCount)
  ) {
    add('pie');
    add('donut');
  }

  // ---- 雷达图（≥2 个数值 yFields + 少量比较对象） ----
  if (numericYFieldCount >= 2 && categoryCount >= 3 && categoryCount <= 12 && canBuild('radar')) {
    add('radar');
  }

  // ---- 组合图（xField + ≥2 数值 yFields，柱状+折线双轴） ----
  if (xField && numericYFieldCount >= 2 && canBuild('combo')) {
    add('combo');
  }

  // ---- 散点图（xField 和 yField 都是数值列） ----
  if (xField && isNumericField(rows, xField) && yNumeric && canBuild('scatter')) {
    add('scatter');
  }

  // ---- 气泡图（散点 + sizeField 是数值列） ----
  if (sizeField && canBuild('bubble')) {
    add('bubble');
  }

  // ---- 热力图（两个类别字段 + valueField） ----
  if (valueField && canBuild('heatmap')) {
    add('heatmap');
  }

  // ---- 箱线图（类别 xField + 数值 valueField） ----
  if (valueField && canBuild('boxplot')) {
    add('boxplot');
  }

  // ---- 仪表盘（单个 KPI 值） ----
  if (valueField && canBuild('gauge')) {
    add('gauge');
  }

  const candidates = [...set];

  // 兜底：至少保留一个安全类型
  if (candidates.length === 0 && canBuild('bar')) {
    candidates.push('bar');
  }

  // 排序：推荐类型 → 横向柱状图（类别多/名称长）→ 普通柱状图 → 其余按优先级
  const priority: Record<RenderableChartType, number> = {
    bar: 0, horizontal_bar: 1, line: 2, area: 3,
    pie: 4, donut: 5, scatter: 6, bubble: 7,
    radar: 8, heatmap: 9, boxplot: 10, gauge: 11, combo: 12,
  };

  candidates.sort((a, b) => {
    if (a === recommended) return -1;
    if (b === recommended) return 1;
    // 类别多或名称长 → 横向柱状图优先于普通柱状图
    if (a === 'horizontal_bar' && b === 'bar' && (categoryCount > 8 || maxCategoryLen > 6)) return -1;
    if (b === 'horizontal_bar' && a === 'bar' && (categoryCount > 8 || maxCategoryLen > 6)) return 1;
    return (priority[a] ?? 99) - (priority[b] ?? 99);
  });

  return candidates.slice(0, 4);
}

/**
 * 评估全部 13 种图表类型对当前数据的可用性。
 * 为每种类型构造最佳 spec（补全必要字段映射），再通过 buildChartOption 验证。
 */
export function getChartTypeAvailability(chart: ChartData): ChartTypeAvailability[] {
  const { columns, rows, spec } = chart;

  // 列分类
  const numericCols = columns.filter(c => isNumericField(rows, c));
  const categoricalCols = columns.filter(c => !isNumericField(rows, c));
  const orderedCols = columns.filter(c => isOrderedField(rows, c));

  // 从已有 spec 取字段（如果存在且合法）
  const specX = spec.xField && columns.includes(spec.xField) ? spec.xField : null;
  const specY0 = spec.yFields?.[0] && columns.includes(spec.yFields[0]) ? spec.yFields[0] : null;
  const specY1 = spec.yFields?.[1] && columns.includes(spec.yFields[1]) ? spec.yFields[1] : null;
  const specVal = spec.valueField && columns.includes(spec.valueField) ? spec.valueField : null;
  const specSize = spec.sizeField && columns.includes(spec.sizeField) ? spec.sizeField : null;

  // 最佳候选字段
  const bestX = specX ?? categoricalCols[0] ?? columns[0] ?? null;
  const bestOrderedX = specX ?? orderedCols[0] ?? null;
  const bestNum1 = specY0 && numericCols.includes(specY0) ? specY0 : numericCols[0] ?? null;
  const bestNum2 = specY1 && numericCols.includes(specY1) ? specY1 : numericCols[1] ?? null;
  const bestNum3 = numericCols[2] ?? null;
  const bestVal = specVal ?? bestNum1;

  /** 构造测试 spec 并调用 buildChartOption 验证 */
  function check(type: RenderableChartType, overrides: Partial<ChartSpec>): boolean {
    const testSpec: ChartSpec = {
      type,
      title: spec.title,
      xField: null,
      yFields: [],
      seriesField: null,
      sizeField: null,
      valueField: null,
      ...overrides,
    };
    // 清理：确保不残留无关字段
    return buildChartOption({ ...chart, spec: testSpec }) !== null;
  }

  function result(type: RenderableChartType, supported: boolean, reason: string): ChartTypeAvailability {
    return { type, label: CHART_TYPE_LABELS[type], supported, reason: supported ? '' : reason };
  }

  const items: ChartTypeAvailability[] = [];

  for (const type of RENDERABLE_TYPES) {
    switch (type) {
      // ── 柱状图 / 横向柱状图：类别 xField + 数值 yField ──
      case 'bar':
      case 'horizontal_bar': {
        if (!bestX || !bestNum1) {
          items.push(result(type, false, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else {
          const ok = check(type, { xField: bestX, yFields: [bestNum1] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 折线图：有序 xField + 数值 yField ──
      case 'line': {
        if (!bestOrderedX || !bestNum1) {
          items.push(result(type, false, '需要有序横轴（时间/月份等）和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else {
          const ok = check(type, { xField: bestOrderedX, yFields: [bestNum1] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 面积图：有序 xField + 数值 yField（比折线图更严格） ──
      case 'area': {
        if (!bestOrderedX || !bestNum1) {
          items.push(result(type, false, '需要有序横轴（时间/月份等）和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else if (!isOrderedField(rows, bestOrderedX)) {
          items.push(result(type, false, '面积图需要有序横轴（时间/月份等）'));
        } else {
          const ok = check(type, { xField: bestOrderedX, yFields: [bestNum1] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 饼图 / 环形图：类别 xField + 数值 yField ──
      case 'pie':
      case 'donut': {
        if (!bestX || !bestNum1) {
          items.push(result(type, false, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else {
          const ok = check(type, { xField: bestX, yFields: [bestNum1] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 散点图：数值 xField + 数值 yField ──
      case 'scatter': {
        const sx = bestNum1;
        const sy = bestNum2;
        if (!sx || !sy) {
          items.push(result(type, false, '需要两个数值列'));
        } else if (!isNumericField(rows, sx) || !isNumericField(rows, sy)) {
          items.push(result(type, false, '两个轴均需为数字类型'));
        } else {
          const ok = check(type, { xField: sx, yFields: [sy] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 气泡图：数值 xField + 数值 yField + 数值 sizeField ──
      case 'bubble': {
        const bx = bestNum1;
        const by = bestNum2;
        const bs = specSize ?? bestNum3;
        if (!bx || !by || !bs) {
          items.push(result(type, false, '需要三个数值列（X/Y/大小）'));
        } else if (
          !isNumericField(rows, bx) ||
          !isNumericField(rows, by) ||
          !isNumericField(rows, bs)
        ) {
          items.push(result(type, false, 'X/Y/大小列均需为数字类型'));
        } else {
          const ok = check(type, { xField: bx, yFields: [by], sizeField: bs });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 雷达图：类别 xField + ≥2 数值 yFields ──
      case 'radar': {
        if (numericCols.length < 2) {
          items.push(result(type, false, '需要至少两个数值列'));
        } else if (!bestX) {
          items.push(result(type, false, '需要分类列作为指标维度'));
        } else {
          const yAll = numericCols.slice(0, 4); // 最多取4个数值列
          const ok = check(type, { xField: bestX, yFields: yAll });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 热力图：横轴 xField + 纵轴 yField + 数值 valueField ──
      case 'heatmap': {
        const hx = bestX;
        const hy = categoricalCols.length >= 2 ? categoricalCols[1] : columns[1] ?? null;
        const hv = bestVal;
        if (!hx || !hy || !hv) {
          items.push(result(type, false, '需要两个分类列和一个数值列'));
        } else if (!isNumericField(rows, hv)) {
          items.push(result(type, false, '热力值列必须为数字类型'));
        } else {
          const ok = check(type, { xField: hx, yFields: [hy], valueField: hv });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 箱线图：类别 xField + 数值 valueField ──
      case 'boxplot': {
        if (!bestX || !bestVal) {
          items.push(result(type, false, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestVal)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else {
          const ok = check(type, { xField: bestX, valueField: bestVal });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 仪表盘：单个数值 valueField ──
      case 'gauge': {
        if (!bestVal) {
          items.push(result(type, false, '需要数值列作为指标值'));
        } else if (!isNumericField(rows, bestVal)) {
          items.push(result(type, false, '数值列必须为数字类型'));
        } else {
          const ok = check(type, { valueField: bestVal });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 组合图：类别 xField + ≥2 数值 yFields ──
      case 'combo': {
        if (numericCols.length < 2) {
          items.push(result(type, false, '需要至少两个数值列（双轴）'));
        } else if (!bestX) {
          items.push(result(type, false, '需要分类列作为横轴'));
        } else {
          const ok = check(type, { xField: bestX, yFields: [numericCols[0], numericCols[1]] });
          items.push(result(type, ok, ok ? '' : '该数据类型暂不支持该图表'));
        }
        break;
      }

      default:
        items.push(result(type, false, '未知图表类型'));
    }
  }

  return items;
}

export function buildChartOption(chart: ChartData): EChartsOption | null {
  if (!isRenderableChartType(chart.spec.type)) return null;
  const builder = CHART_BUILDERS[chart.spec.type];
  return builder ? builder(chart) : null;
}

function getField(spec: ChartSpec, key: 'xField' | 'sizeField' | 'valueField'): string | null {
  const value = spec[key];
  return typeof value === 'string' && value.trim() ? value : null;
}

function getYField(spec: ChartSpec): string | null {
  const value = spec.yFields?.[0];
  return typeof value === 'string' && value.trim() ? value : null;
}

function hasColumn(columns: string[], field: string | null): field is string {
  return !!field && columns.includes(field);
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function isNumericField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(row => row[field]).filter(value => !isNullValue(value));
  if (!values.length) return false;
  return values.every(value => toNumber(value) !== null);
}

/** 识别具有自然顺序的字符串：月份/季度/年份/日期，用于 line/area 横轴兼容判断 */
function isOrderedStringValue(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  // ISO 日期
  if (!isNaN(new Date(value).getTime())) return true;
  // 中文日期：2024年1月
  if (/^\d{4}年\d{1,2}月$/.test(value)) return true;
  // 中文季度：2024年第1季度
  if (/^\d{4}年第\d{1,2}季度$/.test(value)) return true;
  // 月份：1月 ~ 12月
  if (/^\d{1,2}月$/.test(value)) return true;
  // 季度：Q1 ~ Q4
  if (/^Q[1-4]$/i.test(value)) return true;
  // 纯年份：2020
  if (/^\d{4}$/.test(value)) return true;
  return false;
}

function isOrderedField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(row => row[field]).filter(value => !isNullValue(value));
  if (!values.length) return false;
  return values.every(value => typeof value === 'number' || isOrderedStringValue(value));
}

/** 将 xField 值转为可比较的排序键（时间→时间戳，数值→数字，有序字符串→数值，其它→字符串） */
function xFieldSortKey(value: unknown): number | string {
  if (typeof value === 'number') return value;
  if (typeof value !== 'string') return String(value);
  // ISO 日期
  const d = new Date(value);
  if (!isNaN(d.getTime())) return d.getTime();
  // 中文日期：2024年1月
  const cnDate = value.match(/^(\d{4})年(\d{1,2})月$/);
  if (cnDate) {
    return new Date(`${cnDate[1]}-${cnDate[2].padStart(2, '0')}-01`).getTime();
  }
  // 中文季度：2024年第1季度 → 2024*10+1 = 20241
  const cnQ = value.match(/^(\d{4})年第(\d{1,2})季度$/);
  if (cnQ) return Number(cnQ[1]) * 10 + Number(cnQ[2]);
  // 月份：1月 ~ 12月 → 1~12
  const month = value.match(/^(\d{1,2})月$/);
  if (month) return Number(month[1]);
  // 季度：Q1 ~ Q4 → 1~4
  const q = value.match(/^Q([1-4])$/i);
  if (q) return Number(q[1]);
  // 纯数字字符串（含纯年份）
  const n = Number(value);
  if (Number.isFinite(n)) return n;
  return value;
}

function cleanRows(rows: Row[], fields: string[]): Row[] {
  return rows.filter(row => fields.every(field => !isNullValue(row[field])));
}

function baseTitle(chart: ChartData): string {
  return chart.spec.title || chart.title || '数据可视化';
}

function buildAxisChart(chart: ChartData, mode: 'bar' | 'horizontal_bar' | 'line' | 'area'): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yField = getYField(chart.spec);
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, yField)) return null;
  if (!isNumericField(chart.rows, yField)) return null;
  if (mode === 'area' && !isOrderedField(chart.rows, xField)) return null;
  // 显式指定折线图时允许分类字段作为 category X 轴，非显式仍要求有序字段
  if (mode === 'line' && !chart.explicitType && !isOrderedField(chart.rows, xField)) return null;

  const rows = cleanRows(chart.rows, [xField, yField]);
  if (!rows.length) return null;
  const horizontal = mode === 'horizontal_bar';
  const line = mode === 'line' || mode === 'area';

  // line/area 按横轴值排序，确保折线按正确顺序连线
  const sortedRows = line
    ? [...rows].sort((a, b) => {
        const ka = xFieldSortKey(a[xField]);
        const kb = xFieldSortKey(b[xField]);
        if (typeof ka === 'number' && typeof kb === 'number') return ka - kb;
        return String(ka).localeCompare(String(kb));
      })
    : rows;

  const labels = sortedRows.map(row => String(row[xField]));
  const values = sortedRows.map(row => toNumber(row[yField]) ?? 0);

  const ySummable = isSummable(yField);
  const hasNegative = values.some((v: number) => v < 0);
  const vTotal = values.reduce((s: number, v: number) => s + v, 0);
  const showBarProportion = !line && ySummable && !hasNegative && vTotal > 0;

  // 竖向柱状图：判断是否需要旋转标签（数量多或名称长）
  const maxBarLabelLen = labels.length > 0 ? Math.max(...labels.map(s => s.length)) : 0;
  const barNeedsRotate = labels.length > 6 || (labels.length > 3 && maxBarLabelLen > 8);

  // 竖向柱状图每个标签的最大宽度（防止长名称互相覆盖）
  const barLabelWidth = labels.length > 0
    ? Math.max(48, Math.min(140, Math.floor(560 / labels.length)))
    : undefined;

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: {
      trigger: 'axis',
      formatter: line
        ? (params: { seriesName: string; name: string; value: number }[]) => {
            const p = params[0];
            if (!p) return '';
            return `${p.name}<br/>${p.seriesName || yField}：${p.value}`;
          }
        : (params: { name: string; value: number }[]) => {
            const p = params[0];
            if (!p) return '';
            if (showBarProportion) {
              const pctVal = ((p.value / vTotal) * 100).toFixed(1);
              const rank = values.filter((v: number) => v > p.value).length + 1;
              return `${yField}<br/>${p.name}：${p.value}<br/>占比：${pctVal}%<br/>排名：第${rank}/${values.length}`;
            }
            return `${yField}<br/>${p.name}：${p.value}`;
          },
    },
    grid: { bottom: horizontal ? 40 : 80, top: 50, left: horizontal ? 120 : 50, right: 24 },
    xAxis: horizontal
      ? { type: 'value', name: yField }
      : (
        mode === 'bar'
          ? {
              type: 'category',
              data: labels,
              axisLabel: {
                interval: 0,
                rotate: barNeedsRotate ? 45 : 0,
                fontSize: 11,
                overflow: 'truncate',
                width: barLabelWidth,
              },
            }
          : { type: 'category', data: labels, axisLabel: { rotate: labels.length > 6 ? 45 : 0, fontSize: 11 } }
      ),
    yAxis: horizontal
      ? { type: 'category', data: labels, axisLabel: { fontSize: 11 } }
      : { type: 'value', name: yField },
    series: [{
      type: line ? 'line' : 'bar',
      data: values,
      smooth: line,
      areaStyle: mode === 'area' ? {} : undefined,
      itemStyle: { color: '#2563eb' },
      symbol: line ? 'circle' : undefined,
      symbolSize: line ? 6 : undefined,
    }],
  };
}

function buildPieChart(chart: ChartData, donut = false): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yField = getYField(chart.spec);
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, yField)) return null;
  if (!isNumericField(chart.rows, yField)) return null;

  const rows = cleanRows(chart.rows, [xField, yField]);
  if (!rows.length) return null;

  // 按值降序排列，预计算排名和总量用于 tooltip
  const items = rows
    .map(row => ({ name: String(row[xField]), value: toNumber(row[yField]) ?? 0 }))
    .sort((a, b) => b.value - a.value);
  const total = items.reduce((s, i) => s + i.value, 0);

  const pieData = items.map((item, i) => ({ ...item, _rank: i + 1 }));
  const tooltipFormatter = total > 0
    ? (p: { name: string; value: number; percent: number; data: { _rank: number } }) =>
        `${p.name}<br/>数值：${p.value}<br/>占比：${p.percent}%<br/>排名：第${p.data._rank}/${items.length}`
    : undefined;

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: { trigger: 'item', formatter: tooltipFormatter },
    legend: { type: 'scroll', orient: 'horizontal', bottom: 0, textStyle: { fontSize: 11 } },
    series: [{
      type: 'pie',
      radius: donut ? ['42%', '70%'] : '70%',
      center: ['50%', '50%'],
      data: pieData,
      label: { fontSize: 11, overflow: 'truncate', width: 80, formatter: '{b}: {d}%' },
      emphasis: { label: { overflow: 'none', width: undefined, fontSize: 13, formatter: '{b}\n{c} ({d}%)' } },
    }],
  };
}

function buildScatterChart(chart: ChartData, bubble = false): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yField = getYField(chart.spec);
  const sizeField = getField(chart.spec, 'sizeField');
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, yField)) return null;
  if (!isNumericField(chart.rows, xField) || !isNumericField(chart.rows, yField)) return null;
  if (bubble && (!hasColumn(chart.columns, sizeField) || !isNumericField(chart.rows, sizeField))) return null;

  const fields = bubble && sizeField ? [xField, yField, sizeField] : [xField, yField];
  const rows = cleanRows(chart.rows, fields);
  if (!rows.length) return null;
  const sizes = bubble && sizeField ? rows.map(row => toNumber(row[sizeField]) ?? 0) : [];
  const maxSize = sizes.length ? Math.max(...sizes, 1) : 1;

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: { trigger: 'item' },
    grid: { bottom: 60, top: 50, left: 60, right: 24 },
    xAxis: { type: 'value', name: xField },
    yAxis: { type: 'value', name: yField },
    series: [{
      type: 'scatter',
      data: rows.map(row => {
        const point = [toNumber(row[xField]) ?? 0, toNumber(row[yField]) ?? 0];
        return bubble && sizeField ? [...point, toNumber(row[sizeField]) ?? 0] : point;
      }),
      symbolSize: bubble ? (value: number[]) => Math.max(8, Math.min(48, (Number(value[2]) / maxSize) * 42)) : 10,
      itemStyle: { color: '#2563eb', opacity: 0.78 },
    }],
  };
}

/** radar：xField=指标名，yFields=多个数值系列 */
function buildRadarChart(chart: ChartData): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yFields = (chart.spec.yFields ?? []).filter((f): f is string => typeof f === 'string' && f.trim().length > 0);
  if (!hasColumn(chart.columns, xField) || yFields.length === 0) return null;
  for (const yf of yFields) {
    if (!hasColumn(chart.columns, yf) || !isNumericField(chart.rows, yf)) return null;
  }

  const fields = [xField, ...yFields];
  const rows = cleanRows(chart.rows, fields);
  if (!rows.length) return null;

  // 每个指标取所有系列中的最大值，加 20% 余量作为轴上限
  const indicatorMax: Record<string, number> = {};
  for (const row of rows) {
    const name = String(row[xField]);
    let maxVal = 0;
    for (const yf of yFields) {
      maxVal = Math.max(maxVal, toNumber(row[yf]) ?? 0);
    }
    indicatorMax[name] = Math.max(indicatorMax[name] ?? 0, maxVal);
  }

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: { trigger: 'item' },
    legend: { type: 'scroll', orient: 'horizontal', bottom: 0, textStyle: { fontSize: 11 } },
    radar: {
      indicator: rows.map(row => {
        const name = String(row[xField]);
        return { name, max: Math.ceil((indicatorMax[name] || 1) * 1.2) };
      }),
      center: ['50%', '55%'],
      radius: '65%',
    },
    series: yFields.map((yf, i) => ({
      type: 'radar',
      data: [{ value: rows.map(row => toNumber(row[yf]) ?? 0), name: yf }],
      symbol: 'circle',
      symbolSize: 4,
      lineStyle: { width: 2 },
      itemStyle: { color: BLUE_PALETTE[i % BLUE_PALETTE.length] },
    })),
  };
}

/** heatmap：xField=横轴，yField=纵轴，valueField=热力值 */
function buildHeatmapChart(chart: ChartData): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yField = getYField(chart.spec);
  const valueField = getField(chart.spec, 'valueField');
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, yField) || !hasColumn(chart.columns, valueField)) return null;
  if (!isNumericField(chart.rows, valueField)) return null;

  const rows = cleanRows(chart.rows, [xField, yField, valueField]);
  if (!rows.length) return null;

  // 按出现顺序去重保持稳定
  const seenX = new Set<string>();
  const xCategories = rows.map(r => String(r[xField])).filter(v => !seenX.has(v) && seenX.add(v));
  const seenY = new Set<string>();
  const yCategories = rows.map(r => String(r[yField])).filter(v => !seenY.has(v) && seenY.add(v));

  const xIndex: Record<string, number> = {};
  xCategories.forEach((c, i) => { xIndex[c] = i; });
  const yIndex: Record<string, number> = {};
  yCategories.forEach((c, i) => { yIndex[c] = i; });

  const values = rows.map(row => toNumber(row[valueField]) ?? 0);
  const minVal = Math.min(...values, 0);
  const maxVal = Math.max(...values, 1);

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: { trigger: 'item' },
    grid: { bottom: 80, top: 50, left: 120, right: 60 },
    xAxis: { type: 'category', data: xCategories, axisLabel: { rotate: xCategories.length > 6 ? 45 : 0, fontSize: 11 } },
    yAxis: { type: 'category', data: yCategories, axisLabel: { fontSize: 11 } },
    visualMap: {
      min: minVal,
      max: maxVal,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      inRange: { color: ['#eff6ff', '#2563eb'] },
    },
    series: [{
      type: 'heatmap',
      data: rows.map(row => [xIndex[String(row[xField])], yIndex[String(row[yField])], toNumber(row[valueField]) ?? 0]),
      label: { show: rows.length <= 30, fontSize: 11 },
    }],
  };
}

/** 计算分位数 (线性插值) */
function percentile(sorted: number[], p: number): number {
  const n = sorted.length;
  if (n === 0) return 0;
  if (n === 1) return sorted[0];
  const idx = (n - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] * (hi - idx) + sorted[hi] * (idx - lo);
}

/** boxplot：xField=分组字段，valueField=数值字段，前端计算五数概括 */
function buildBoxplotChart(chart: ChartData): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const valueField = getField(chart.spec, 'valueField');
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, valueField)) return null;
  if (!isNumericField(chart.rows, valueField)) return null;

  const rows = cleanRows(chart.rows, [xField, valueField]);
  if (!rows.length) return null;

  // 按 xField 分组
  const groups = new Map<string, number[]>();
  for (const row of rows) {
    const key = String(row[xField]);
    const v = toNumber(row[valueField]);
    if (v === null) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(v);
  }
  if (!groups.size) return null;

  const groupNames = [...groups.keys()];
  const boxData = groupNames.map(name => {
    const vals = groups.get(name)!.sort((a, b) => a - b);
    return [
      vals[0],                          // min
      percentile(vals, 0.25),           // Q1
      percentile(vals, 0.5),            // median
      percentile(vals, 0.75),           // Q3
      vals[vals.length - 1],            // max
    ];
  });

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: { trigger: 'item' },
    grid: { bottom: 60, top: 50, left: 60, right: 24 },
    xAxis: { type: 'category', data: groupNames, axisLabel: { rotate: groupNames.length > 6 ? 45 : 0, fontSize: 11 } },
    yAxis: { type: 'value', name: valueField },
    series: [{
      type: 'boxplot',
      data: boxData,
      itemStyle: { color: '#2563eb', borderColor: '#1e40af' },
    }],
  };
}

/** gauge：valueField=数值字段，取首行；可选 min/max/unit */
function buildGaugeChart(chart: ChartData): EChartsOption | null {
  const valueField = getField(chart.spec, 'valueField');
  if (!hasColumn(chart.columns, valueField)) return null;
  if (!isNumericField(chart.rows, valueField)) return null;

  const rows = cleanRows(chart.rows, [valueField]);
  if (!rows.length) return null;

  const value = toNumber(rows[0][valueField]) ?? 0;
  const min = typeof chart.spec.min === 'number' ? chart.spec.min : 0;
  const max = typeof chart.spec.max === 'number' ? chart.spec.max : Math.max(value * 1.5, 100);
  const unit = typeof chart.spec.unit === 'string' ? chart.spec.unit : '';

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    series: [{
      type: 'gauge',
      min,
      max,
      radius: '80%',
      center: ['50%', '58%'],
      axisLine: { lineStyle: { color: [[0.3, '#2563eb'], [0.7, '#93c5fd'], [1, '#e5e7eb']], width: 20 } },
      pointer: { length: '70%', width: 6 },
      detail: { formatter: `{value}${unit}`, fontSize: 20, offsetCenter: [0, '60%'] },
      data: [{ value, name: valueField }],
    }],
  };
}

/** combo：分类 X 轴 + 双数值 Y 轴（柱状 left + 折线 right） */
function buildComboChart(chart: ChartData): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  if (!hasColumn(chart.columns, xField)) return null;

  // 从 spec.yFields 取已指定的数值字段
  const specYFields = (chart.spec.yFields ?? []).filter((f): f is string => typeof f === 'string' && f.trim().length > 0);

  // spec 已有 ≥2 个数值 yField → 直接用前两个
  if (specYFields.length >= 2) {
    const y1 = specYFields[0];
    const y2 = specYFields[1];
    if (!isNumericField(chart.rows, y1) || !isNumericField(chart.rows, y2)) return null;
    return buildComboOption(chart, xField, y1, y2);
  }

  // spec 只有 1 个 yField → 从数据列中找第二个数值字段（排除 xField 和 y1）
  if (specYFields.length === 1) {
    const y1 = specYFields[0];
    if (!isNumericField(chart.rows, y1)) return null;
    const y2 = chart.columns.find(
      c => c !== xField && c !== y1 && isNumericField(chart.rows, c),
    );
    if (!y2) return null;
    return buildComboOption(chart, xField, y1, y2);
  }

  // 没有 yField → 从数据列中找前两个数值字段（排除 xField）
  const numFields = chart.columns.filter(
    c => c !== xField && isNumericField(chart.rows, c),
  );
  if (numFields.length < 2) return null;
  return buildComboOption(chart, xField, numFields[0], numFields[1]);
}

/** 组装 combo ECharts option */
function buildComboOption(
  chart: ChartData,
  xField: string,
  y1: string,
  y2: string,
): EChartsOption | null {
  const rows = cleanRows(chart.rows, [xField, y1, y2]);
  if (!rows.length) return null;

  const labels = rows.map(row => String(row[xField]));
  const barData = rows.map(row => toNumber(row[y1]) ?? 0);
  const lineData = rows.map(row => toNumber(row[y2]) ?? 0);

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: {
      trigger: 'axis',
      formatter: (params: { seriesName: string; name: string; value: number }[]) => {
        if (!params.length) return '';
        const x = params[0].name;
        let html = `${x}`;
        for (const p of params) {
          html += `<br/>${p.seriesName}：${p.value}`;
        }
        return html;
      },
    },
    legend: {
      data: [y1, y2],
      type: 'scroll',
      orient: 'horizontal',
      bottom: 0,
      textStyle: { fontSize: 11 },
    },
    grid: { bottom: 60, top: 60, left: 60, right: 80 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { rotate: labels.length > 6 ? 45 : 0, fontSize: 11 },
    },
    yAxis: [
      { type: 'value', name: y1, nameTextStyle: { fontSize: 11, color: '#6b7280' } },
      { type: 'value', name: y2, nameTextStyle: { fontSize: 11, color: '#6b7280' } },
    ],
    series: [
      {
        name: y1,
        type: 'bar',
        data: barData,
        yAxisIndex: 0,
        itemStyle: { color: '#2563eb' },
      },
      {
        name: y2,
        type: 'line',
        data: lineData,
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        itemStyle: { color: '#f97316' },
        lineStyle: { color: '#f97316' },
      },
    ],
  };
}

const CHART_BUILDERS: Record<RenderableChartType, (chart: ChartData) => EChartsOption | null> = {
  bar: chart => buildAxisChart(chart, 'bar'),
  horizontal_bar: chart => buildAxisChart(chart, 'horizontal_bar'),
  line: chart => buildAxisChart(chart, 'line'),
  area: chart => buildAxisChart(chart, 'area'),
  pie: chart => buildPieChart(chart),
  donut: chart => buildPieChart(chart, true),
  scatter: chart => buildScatterChart(chart),
  bubble: chart => buildScatterChart(chart, true),
  radar: chart => buildRadarChart(chart),
  heatmap: chart => buildHeatmapChart(chart),
  boxplot: chart => buildBoxplotChart(chart),
  gauge: chart => buildGaugeChart(chart),
  combo: chart => buildComboChart(chart),
};
