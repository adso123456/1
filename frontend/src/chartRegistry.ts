import type { ChartData, ChartSpec, ChartType, ChartTypeAvailability, RenderableChartType } from './types';
import { isSummable } from './chartDescription';
import { formatColumnLabel, formatCellValue } from './utils/tableFormatting';

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

/**
 * 评估全部 13 种图表类型对当前数据的可用性。
 * 为每种类型构造最佳 spec（补全必要字段映射），再通过 buildChartOption 验证。
 * supported=true 时 spec 为通过验证的完整 ChartSpec，可直接用于渲染。
 */
export function getChartTypeAvailability(chart: ChartData): ChartTypeAvailability[] {
  const { columns, rows, spec } = chart;

  // ── 列分类 ──
  const numericCols = columns.filter(c => isNumericField(rows, c));
  // 显式标注 string[]：!isNumericField 的否定类型谓词会被推断为 never[]，导致后续 .includes/.find 报错
  const categoricalCols: string[] = columns.filter(c => !isNumericField(rows, c));

  // ── 从已有 spec 取字段（仅当列存在且满足目标图表语义时才复用） ──
  const specX = spec.xField && columns.includes(spec.xField) ? spec.xField : null;
  const specY0 = spec.yFields?.[0] && columns.includes(spec.yFields[0]) ? spec.yFields[0] : null;
  const specY1 = spec.yFields?.[1] && columns.includes(spec.yFields[1]) ? spec.yFields[1] : null;
  const specVal = spec.valueField && columns.includes(spec.valueField) ? spec.valueField : null;
  const specSize = spec.sizeField && columns.includes(spec.sizeField) ? spec.sizeField : null;
  const specSeries = spec.seriesField && columns.includes(spec.seriesField) ? spec.seriesField : null;

  // ── 最佳候选字段 ──
  // 数值列：优先复用 spec 中已指定且合法的字段
  const bestNum1 = specY0 && numericCols.includes(specY0) ? specY0 : (numericCols[0] ?? null);
  // 分类横轴候选：只有非数值分类列才优先复用 specX，避免数字型 specX（如 COD/排污口数量）
  // 被分类型图表当作名称轴；其次取分类列，最后才考虑其他非指标列。
  const bestCategoryX = (specX && categoricalCols.includes(specX))
    ? specX
    : (categoricalCols.find(c => c !== bestNum1) ?? columns.find(c => c !== bestNum1) ?? null);
  // 有序横轴候选：仅真实时间/顺序维度（字符串日期/月份/季度 + 数字年份），
  // 排除 COD/BOD/温度/浓度/金额等普通数值指标。
  const temporalCols = columns.filter(c => isTemporalField(rows, c) && c !== bestNum1);
  const bestOrderedX = (specX && temporalCols.includes(specX)) ? specX : (temporalCols[0] ?? null);
  const bestNum2 = specY1 && numericCols.includes(specY1) ? specY1 : (numericCols[1] ?? null);
  const bestNum3 = numericCols[2] ?? null;
  const bestVal = specVal ?? bestNum1;
  // 散点/气泡点名称字段：优先复用合法非数值 seriesField，否则第一个分类列；无分类列时为空
  const bestNameField = (specSeries && categoricalCols.includes(specSeries))
    ? specSeries
    : (categoricalCols[0] ?? null);

  // 饼图/环形图 兼容性：仅需非负（分类数量不再作为限制，仅影响可读性）
  const hasNegativeY1 = bestNum1 ? rows.some(r => (toNumber(r[bestNum1]) ?? 0) < 0) : true;

  /** 构造测试 spec（继承原 spec.title） */
  function buildSpec(type: RenderableChartType, overrides: Partial<ChartSpec>): ChartSpec {
    return {
      type,
      title: spec.title,
      xField: null,
      yFields: [],
      seriesField: null,
      sizeField: null,
      valueField: null,
      ...overrides,
    };
  }

  /** 用 buildChartOption 验证，返回完整 ChartSpec 或 null */
  function check(type: RenderableChartType, overrides: Partial<ChartSpec>, opts?: { explicitType?: boolean }): ChartSpec | null {
    const testSpec = buildSpec(type, overrides);
    const testChart: ChartData = { ...chart, spec: testSpec, explicitType: opts?.explicitType ?? false };
    return buildChartOption(testChart) !== null ? testSpec : null;
  }

  /** 生成结果项 */
  function avail(type: RenderableChartType, checked: ChartSpec | null, fallbackReason: string): ChartTypeAvailability {
    return {
      type,
      label: CHART_TYPE_LABELS[type],
      supported: checked !== null,
      spec: checked,
      reason: checked !== null ? '' : fallbackReason,
    };
  }

  const items: ChartTypeAvailability[] = [];

  for (const type of RENDERABLE_TYPES) {
    switch (type) {
      // ── 柱状图 / 横向柱状图：类别 xField + 数值 yField ──
      case 'bar':
      case 'horizontal_bar': {
        if (!bestCategoryX || !bestNum1) {
          items.push(avail(type, null, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
        } else {
          const s = check(type, { xField: bestCategoryX, yFields: [bestNum1] });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 折线图：按数据形态选择横轴（横截面对比 / 单对象趋势 / 多对象时间序列） ──
      case 'line': {
        if (!bestNum1) {
          items.push(avail(type, null, '需要横轴和数值列'));
          break;
        }
        if (!isNumericField(rows, bestNum1)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
          break;
        }

        const entityField = findEntityNameField(columns, rows);
        let lineX: string | null = null;
        let reason = '';

        if (entityField) {
          const { entityCount, maxOccur } = countEntityOccurrences(rows, entityField);
          if (entityCount > 1 && maxOccur === 1) {
            // A 横截面对比：每个对象仅一条记录，用对象名称作横轴，不连成时间趋势
            lineX = entityField;
          } else if (entityCount > 1 && maxOccur > 1 && bestOrderedX) {
            // C 多对象时间序列：当前单系列折线会把不同对象错误连成一条线
            reason = '多对象时间序列需要分系列展示';
          } else if (entityCount <= 1 && bestOrderedX) {
            // B 单对象趋势：用时间作横轴
            lineX = bestOrderedX;
          } else {
            // D 无时间字段（或对象无重复但无时间）：显式切换时回退到对象字段
            lineX = entityField;
          }
        } else if (bestOrderedX) {
          // B 无对象字段的单序列时间趋势
          lineX = bestOrderedX;
        } else {
          // D 无对象无时间：回退分类列（显式切换允许）
          lineX = bestCategoryX;
        }

        if (reason) {
          items.push(avail(type, null, reason));
          break;
        }
        if (!lineX) {
          items.push(avail(type, null, '需要横轴和数值列'));
          break;
        }
        // 防御：横轴不得与数值指标相同（正常规则下不应触发，但必须保留）
        if (lineX === bestNum1) {
          items.push(avail(type, null, '横轴不能与数值指标相同'));
          break;
        }
        const s = check(type, { xField: lineX, yFields: [bestNum1] }, { explicitType: true });
        items.push(avail(type, s, '该数据类型暂不支持该图表'));
        break;
      }

      // ── 面积图：只允许真实时间/顺序维度，不得用普通数值指标作横轴 ──
      case 'area': {
        if (!bestOrderedX || !bestNum1) {
          items.push(avail(type, null, '需要有序横轴（时间/月份等）和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
        } else {
          const s = check(type, { xField: bestOrderedX, yFields: [bestNum1] });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 饼图 / 环形图：分类字段 + 数值字段 + 非负 + 合计>0 ──
      // 注：分类数量多仅影响可读性，不作为 supported 判定条件
      case 'pie':
      case 'donut': {
        if (!bestCategoryX || !bestNum1) {
          items.push(avail(type, null, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestNum1)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
        } else if (hasNegativeY1) {
          items.push(avail(type, null, '包含负数，不适合饼图'));
        } else {
          // 有效数值合计必须大于 0（挡住全为 0 的数据）
          const ySum = rows.reduce((s, r) => s + (toNumber(r[bestNum1!]) ?? 0), 0);
          if (ySum <= 0) {
            items.push(avail(type, null, '数值合计必须大于 0'));
          } else {
            const s = check(type, { xField: bestCategoryX, yFields: [bestNum1] });
            items.push(avail(type, s, '该数据类型暂不支持该图表'));
          }
        }
        break;
      }

      // ── 散点图：数值 xField + 数值 yField（优先复用 spec 合法字段，X/Y 不得相同） ──
      case 'scatter': {
        // 优先复用 spec 中合法的数值 xField/yField，不合法时回退到前两个数值列（需不同）
        const sx = (specX && numericCols.includes(specX)) ? specX : bestNum1;
        const sy = (specY0 && numericCols.includes(specY0) && specY0 !== sx)
          ? specY0
          : (bestNum2 && bestNum2 !== sx ? bestNum2 : (numericCols.find(c => c !== sx) ?? null));
        if (!sx || !sy) {
          items.push(avail(type, null, '需要两个数值列'));
        } else if (sx === sy) {
          items.push(avail(type, null, 'X 轴与 Y 轴必须为不同的数值列'));
        } else if (!isNumericField(rows, sx) || !isNumericField(rows, sy)) {
          items.push(avail(type, null, '两个轴均需为数字类型'));
        } else {
          const s = check(type, { xField: sx, yFields: [sy], seriesField: bestNameField });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 气泡图：数值 xField + 数值 yField + 数值 sizeField（三者必须不同） ──
      case 'bubble': {
        const bx = (specX && numericCols.includes(specX)) ? specX : bestNum1;
        const by = (specY0 && numericCols.includes(specY0) && specY0 !== bx)
          ? specY0
          : (bestNum2 && bestNum2 !== bx ? bestNum2 : (numericCols.find(c => c !== bx) ?? null));
        const bs = (specSize && numericCols.includes(specSize) && specSize !== bx && specSize !== by)
          ? specSize
          : (bestNum3 && bestNum3 !== bx && bestNum3 !== by
              ? bestNum3
              : (numericCols.find(c => c !== bx && c !== by) ?? null));
        if (!bx || !by || !bs) {
          items.push(avail(type, null, '需要三个数值列（X/Y/大小）'));
        } else if (bx === by || bs === bx || bs === by) {
          items.push(avail(type, null, 'X/Y/大小必须为三个不同的数值列'));
        } else if (
          !isNumericField(rows, bx) ||
          !isNumericField(rows, by) ||
          !isNumericField(rows, bs)
        ) {
          items.push(avail(type, null, 'X/Y/大小列均需为数字类型'));
        } else {
          const s = check(type, { xField: bx, yFields: [by], sizeField: bs, seriesField: bestNameField });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 雷达图：类别 xField + ≥2 数值 yFields ──
      case 'radar': {
        if (numericCols.length < 2) {
          items.push(avail(type, null, '需要至少两个数值列'));
        } else if (!bestCategoryX) {
          items.push(avail(type, null, '需要分类列作为指标维度'));
        } else {
          const yAll = numericCols.slice(0, 4);
          const s = check(type, { xField: bestCategoryX, yFields: yAll });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 热力图：两个不同的分类列（xField + yField）+ 数值 valueField ──
      case 'heatmap': {
        // 严格使用分类列，禁止回退到 columns[1]
        const hx = (specX && categoricalCols.includes(specX)) ? specX : (categoricalCols[0] ?? null);
        const hy = hx ? (categoricalCols.find(c => c !== hx) ?? null) : (categoricalCols.length >= 2 ? categoricalCols[1] : null);
        const hv = bestVal;
        if (!hx || !hy || !hv) {
          items.push(avail(type, null, '需要两个不同的分类列和一个数值列'));
        } else if (hx === hy) {
          items.push(avail(type, null, '横轴与纵轴必须是不同的分类列'));
        } else if (hv === hx || hv === hy) {
          items.push(avail(type, null, '热力值列不得与横轴/纵轴相同'));
        } else if (!isNumericField(rows, hv)) {
          items.push(avail(type, null, '热力值列必须为数字类型'));
        } else {
          const s = check(type, { xField: hx, yFields: [hy], valueField: hv });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 箱线图：类别 xField + 数值 valueField ──
      case 'boxplot': {
        if (!bestCategoryX || !bestVal) {
          items.push(avail(type, null, '需要分类列和数值列'));
        } else if (!isNumericField(rows, bestVal)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
        } else {
          const s = check(type, { xField: bestCategoryX, valueField: bestVal });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      // ── 仪表盘：仅允许单值 KPI（清洗后仅 1 行数据） ──
      case 'gauge': {
        if (!bestVal) {
          items.push(avail(type, null, '需要数值列作为指标值'));
        } else if (!isNumericField(rows, bestVal)) {
          items.push(avail(type, null, '数值列必须为数字类型'));
        } else {
          const cleanVals = rows.filter(r => !isNullValue(r[bestVal!]));
          if (cleanVals.length > 1) {
            items.push(avail(type, null, '仪表盘仅适用于单值 KPI'));
          } else {
            // 仅保留原始合法的 min/max/unit（number/string），避免被其他图表继承
            const gaugeExtra: Partial<ChartSpec> = {};
            if (typeof spec.min === 'number') gaugeExtra.min = spec.min;
            if (typeof spec.max === 'number') gaugeExtra.max = spec.max;
            if (typeof spec.unit === 'string') gaugeExtra.unit = spec.unit;
            const s = check(type, { valueField: bestVal, ...gaugeExtra });
            items.push(avail(type, s, '该数据类型暂不支持该图表'));
          }
        }
        break;
      }

      // ── 组合图：类别 xField + ≥2 数值 yFields ──
      case 'combo': {
        if (numericCols.length < 2) {
          items.push(avail(type, null, '需要至少两个数值列（双轴）'));
        } else if (!bestCategoryX) {
          items.push(avail(type, null, '需要分类列作为横轴'));
        } else {
          const s = check(type, { xField: bestCategoryX, yFields: [numericCols[0], numericCols[1]] });
          items.push(avail(type, s, '该数据类型暂不支持该图表'));
        }
        break;
      }

      default:
        items.push(avail(type, null, '未知图表类型'));
    }
  }

  return items;
}

export function buildChartOption(chart: ChartData): EChartsOption | null {
  if (!isRenderableChartType(chart.spec.type)) return null;
  const builder = CHART_BUILDERS[chart.spec.type];
  return builder ? builder(chart) : null;
}

function getField(spec: ChartSpec, key: 'xField' | 'sizeField' | 'valueField' | 'seriesField'): string | null {
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

/** 字段名是否暗示为年份维度（年/年份/年度/year） */
function isYearLikeFieldName(field: string): boolean {
  return /年|year/i.test(field);
}

/** 数字值是否全部为合理四位年份（1900~2100） */
function isFourDigitYearValues(rows: Row[], field: string): boolean {
  const values = rows.map(r => r[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  return values.every(v => typeof v === 'number' && Number.isInteger(v) && v >= 1900 && v <= 2100);
}

/** 识别真实时间/顺序维度：字符串日期/月份/季度，或数字年份。
 *  排除 COD/BOD/温度/浓度/金额等普通数值指标，避免它们被当作时间横轴。 */
function isTemporalField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(row => row[field]).filter(value => !isNullValue(value));
  if (!values.length) return false;
  // 全为有序字符串（日期/月份/季度/年份字符串）
  if (values.every(value => isOrderedStringValue(value))) return true;
  // 数字列：仅当字段名暗示年份，或值域为合理四位年份时才视为时间维度
  if (values.every(value => typeof value === 'number')) {
    return isYearLikeFieldName(field) || isFourDigitYearValues(rows, field);
  }
  return false;
}

/** 对象名称字段优先级（仅匹配字段名，不匹配具体数据值）。
 *  裸“单位”不作为关键词（可能表示 mg/L 等计量单位）；“单位名称”通过第 2 层识别。 */
const ENTITY_PRIORITY: string[][] = [
  ['排口名称', '监测点名称', '站点名称', '断面名称', '水厂名称'],
  ['企业名称', '公司名称', '单位名称', '项目名称'],
  ['名称', 'name'],
  ['排口', '监测点', '站点', '断面', '企业', '公司', '水厂', '项目', 'site', 'station', 'company'],
];
/** 对象字段需排除的指标/标识词（字段名包含即排除） */
const ENTITY_METRIC_WORDS = ['数量', '个数', '总数', '次数', '金额', '浓度', '含量', '值', '比例', '占比', '平均', '均值', '总量', '排放量', '面积', '长度', '编号', '编码', 'id'];
/** 地区字段名关键词 */
const REGION_KEYWORDS = ['区县', '地区', '区域', '行政区', '城市', 'city', 'region', 'district'];
/** 地区字段需排除的指标/标识词 */
const REGION_METRIC_WORDS = ['数量', '面积', '金额', '值', '率', '比例', '占比', '编号', '编码', 'id'];

/** 字段名是否包含任一排除词（大小写不敏感） */
function containsAnyWord(field: string, words: string[]): boolean {
  const lower = field.toLowerCase();
  return words.some(w => lower.includes(w.toLowerCase()));
}

/** 识别对象名称字段：仅选非数值分类列，排除指标/标识字段，按优先级返回第一个合法字段，无则 null */
function findEntityNameField(columns: string[], rows: Row[]): string | null {
  // 用 Set 缓存数值列，避免 isNumericField 类型谓词把 col 收窄为 never
  const numericCols = new Set(columns.filter(c => isNumericField(rows, c)));
  for (const layer of ENTITY_PRIORITY) {
    for (const col of columns) {
      if (numericCols.has(col)) continue;
      if (containsAnyWord(col, ENTITY_METRIC_WORDS)) continue;
      if (layer.some(kw => col.toLowerCase().includes(kw.toLowerCase()))) return col;
    }
  }
  return null;
}

/** 识别地区字段：仅选非数值分类列，排除指标/标识字段，返回第一个合法字段，无则 null */
function findRegionField(columns: string[], rows: Row[]): string | null {
  const numericCols = new Set(columns.filter(c => isNumericField(rows, c)));
  for (const col of columns) {
    if (numericCols.has(col)) continue;
    if (containsAnyWord(col, REGION_METRIC_WORDS)) continue;
    if (REGION_KEYWORDS.some(kw => col.toLowerCase().includes(kw.toLowerCase()))) return col;
  }
  return null;
}

/** 统计对象字段的不同值数量与单个对象最大出现次数（基于非空有效行） */
function countEntityOccurrences(rows: Row[], entityField: string): { entityCount: number; maxOccur: number } {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const v = row[entityField];
    if (isNullValue(v)) continue;
    const key = String(v);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  let maxOccur = 0;
  for (const c of counts.values()) if (c > maxOccur) maxOccur = c;
  return { entityCount: counts.size, maxOccur };
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
  // 防御：xField 不得与 yField 相同，避免数值指标列同时充当名称轴
  if (xField === yField) return null;
  if (!isNumericField(chart.rows, yField)) return null;
  // area 必须为真实时间/顺序维度，不得用普通数值指标（COD/温度等）作横轴
  if (mode === 'area' && !isTemporalField(chart.rows, xField)) return null;
  // 显式指定折线图时允许分类字段作为 category X 轴，非显式仍要求真实时间维度
  if (mode === 'line' && !chart.explicitType && !isTemporalField(chart.rows, xField)) return null;

  const rows = cleanRows(chart.rows, [xField, yField]);
  if (!rows.length) return null;
  const horizontal = mode === 'horizontal_bar';
  const line = mode === 'line' || mode === 'area';

  // 折线图字段语义：对象名称、地区、横轴是否为时间字段
  const entityField = findEntityNameField(chart.columns, chart.rows);
  const regionField = findRegionField(chart.columns, chart.rows);
  const xIsTemporal = isTemporalField(chart.rows, xField);
  const xIsEntity = mode === 'line' && xField === entityField;
  // 非横轴的时间字段：横轴为对象/分类时，tooltip 用它显示监测日期
  const dateField = (mode === 'line' && !xIsTemporal)
    ? (chart.columns.find(c => isTemporalField(chart.rows, c) && c !== xField && c !== yField) ?? null)
    : null;

  // 排序：area 始终按时间；line 仅在横轴为时间字段时按时间排序，对象/分类横轴保留 SQL 原始行顺序
  const needSort = mode === 'area' || (mode === 'line' && xIsTemporal);
  const sortedRows = needSort
    ? [...rows].sort((a, b) => {
        const ka = xFieldSortKey(a[xField]);
        const kb = xFieldSortKey(b[xField]);
        if (typeof ka === 'number' && typeof kb === 'number') return ka - kb;
        return String(ka).localeCompare(String(kb));
      })
    : rows;

  // 折线图横轴标签：时间值去掉 T00:00:00；对象横轴可拼接"地区 · 对象名"（不重复）
  const makeLineLabel = (row: Row): string => {
    if (xIsTemporal) return formatCellValue(row[xField]);
    const nameVal = formatCellValue(row[xField]);
    if (xIsEntity && regionField && regionField !== xField && regionField !== yField) {
      const regionVal = formatCellValue(row[regionField]);
      if (regionVal && regionVal !== '—' && !nameVal.includes(regionVal) && !regionVal.includes(nameVal)) {
        return `${regionVal} · ${nameVal}`;
      }
    }
    return nameVal;
  };
  const labels = mode === 'line'
    ? sortedRows.map(row => makeLineLabel(row))
    : sortedRows.map(row => String(row[xField]));
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

  const yLabel = formatColumnLabel(yField);
  const xLabel = formatColumnLabel(xField);

  return {
    color: BLUE_PALETTE,
    title: { text: baseTitle(chart), left: 'center', textStyle: { fontSize: 14, color: '#374151' } },
    tooltip: {
      trigger: 'axis',
      formatter: line
        ? (params: { dataIndex: number; seriesName: string; name: string; value: number }[]) => {
            const p = params[0];
            if (!p) return '';
            // 通过 dataIndex 对应到实际行，按存在情况显示对象/地区/日期/指标，文本去重
            const row = sortedRows[p.dataIndex];
            if (!row) return '';
            const parts: string[] = [];
            const seen = new Set<string>();
            // 对象名称（第一行，无前缀）
            if (entityField && entityField !== yField) {
              const v = formatCellValue(row[entityField]);
              if (v && v !== '—') { parts.push(v); seen.add(v); }
            }
            // 地区
            if (regionField && regionField !== yField && regionField !== entityField) {
              const v = formatCellValue(row[regionField]);
              if (v && v !== '—' && !seen.has(v)) {
                parts.push(`${formatColumnLabel(regionField)}：${v}`);
                seen.add(v);
              }
            }
            // 监测日期（仅当横轴非时间时显示，避免与横轴重复）
            if (dateField) {
              const v = formatCellValue(row[dateField]);
              if (v && v !== '—' && !seen.has(v)) {
                parts.push(`${formatColumnLabel(dateField)}：${v}`);
                seen.add(v);
              }
            }
            // 指标名称与数值
            parts.push(`${yLabel}：${formatCellValue(row[yField])}`);
            return parts.join('<br/>');
          }
        : (params: { name: string; value: number }[]) => {
            const p = params[0];
            if (!p) return '';
            if (showBarProportion) {
              const pctVal = ((p.value / vTotal) * 100).toFixed(1);
              const rank = values.filter((v: number) => v > p.value).length + 1;
              return `${yLabel}<br/>${p.name}：${p.value}<br/>占比：${pctVal}%<br/>排名：第${rank}/${values.length}`;
            }
            return `${yLabel}<br/>${p.name}：${p.value}`;
          },
    },
    grid: { bottom: horizontal ? 40 : 80, top: 50, left: horizontal ? 120 : 50, right: 24 },
    xAxis: horizontal
      ? { type: 'value', name: yLabel }
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
          : {
              type: 'category',
              data: labels,
              name: xLabel,
              axisLabel: { rotate: labels.length > 6 ? 45 : 0, fontSize: 11, overflow: 'truncate', width: barLabelWidth },
            }
      ),
    yAxis: horizontal
      ? { type: 'category', data: labels, axisLabel: { fontSize: 11 } }
      : { type: 'value', name: yLabel },
    series: [{
      // name 设为 yLabel，使 tooltip/legend 显示真实指标名而非默认的 series0
      name: yLabel,
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
  // 防御：分类字段不得与数值字段相同
  if (xField === yField) return null;
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
  const nameField = getField(chart.spec, 'seriesField');
  if (!hasColumn(chart.columns, xField) || !hasColumn(chart.columns, yField)) return null;
  // 防御：X/Y 不得相同；bubble 的 sizeField 不得与 X/Y 相同
  if (xField === yField) return null;
  if (!isNumericField(chart.rows, xField) || !isNumericField(chart.rows, yField)) return null;
  if (bubble) {
    if (!hasColumn(chart.columns, sizeField) || !isNumericField(chart.rows, sizeField)) return null;
    if (sizeField === xField || sizeField === yField) return null;
  }

  const fields = bubble && sizeField ? [xField, yField, sizeField] : [xField, yField];
  const totalRows = chart.rows.length;
  const rows = cleanRows(chart.rows, fields);
  if (!rows.length) return null;

  const xLabel = formatColumnLabel(xField);
  const yLabel = formatColumnLabel(yField);
  const sizeLabel = bubble && sizeField ? formatColumnLabel(sizeField) : '';
  const nameLabel = nameField ? formatColumnLabel(nameField) : '';

  // 气泡尺寸：min-max 归一化后 sqrt 平滑，范围 10~44；全部相同用固定尺寸
  const sizes = bubble && sizeField ? rows.map(row => toNumber(row[sizeField]) ?? 0) : [];
  const minSize = sizes.length ? Math.min(...sizes) : 0;
  const maxSize = sizes.length ? Math.max(...sizes) : 0;
  const sizeSpan = maxSize - minSize;
  const MIN_BUBBLE = 10;
  const MAX_BUBBLE = 44;
  const symbolSize = bubble
    ? (value: number[]) => {
        const v = Number(value[2]);
        if (sizeSpan <= 0) return (MIN_BUBBLE + MAX_BUBBLE) / 2; // 全部相同 → 固定尺寸
        const normalized = (v - minSize) / sizeSpan; // 0~1
        return MIN_BUBBLE + Math.sqrt(normalized) * (MAX_BUBBLE - MIN_BUBBLE);
      }
    : 10;

  // 点数据：携带名称（用于标签与 tooltip）
  const data = rows.map(row => {
    const x = toNumber(row[xField]) ?? 0;
    const y = toNumber(row[yField]) ?? 0;
    const name = nameField ? String(row[nameField] ?? '') : '';
    const value = bubble && sizeField ? [x, y, toNumber(row[sizeField]) ?? 0] : [x, y];
    return { name, value };
  });

  // 标题：散点"Y 与 X 关系"，气泡"Y 与 X 关系（气泡大小：SIZE）"
  const relationText = `${yLabel} 与 ${xLabel} 关系`;
  const titleText = bubble ? `${relationText}（气泡大小：${sizeLabel}）` : relationText;
  // 有效行少于原始行数时，subtext 提示缺失
  const subtext = rows.length < totalRows ? `有效数据 ${rows.length}/${totalRows}，缺失坐标已忽略` : '';

  // 数据点不多且存在名称字段时默认显示标签，较多则仅 emphasis 显示
  const showLabel = !!nameField && data.length <= 12;

  return {
    color: BLUE_PALETTE,
    title: {
      text: titleText,
      subtext,
      left: 'center',
      textStyle: { fontSize: 14, color: '#374151' },
      subtextStyle: { fontSize: 11, color: '#9ca3af' },
    },
    tooltip: {
      trigger: 'item',
      formatter: (p: { name: string; value: number[] }) => {
        const v = p.value;
        const parts: string[] = [];
        if (nameField && p.name) parts.push(`${nameLabel || '名称'}：${p.name}`);
        parts.push(`${xLabel}：${v[0]}`);
        parts.push(`${yLabel}：${v[1]}`);
        if (bubble && sizeField) parts.push(`${sizeLabel}：${v[2]}`);
        return parts.join('<br/>');
      },
    },
    grid: { bottom: 60, top: 60, left: 60, right: 24 },
    xAxis: { type: 'value', name: xLabel, nameTextStyle: { fontSize: 11, color: '#6b7280' } },
    yAxis: { type: 'value', name: yLabel, nameTextStyle: { fontSize: 11, color: '#6b7280' } },
    series: [{
      type: 'scatter',
      data,
      symbolSize,
      itemStyle: { color: '#2563eb', opacity: 0.78 },
      label: {
        show: showLabel,
        position: 'top',
        fontSize: 11,
        color: '#374151',
        formatter: (p: { name: string }) => p.name,
        overflow: 'truncate',
        width: 80,
      },
      emphasis: {
        label: {
          show: !!nameField,
          position: 'top',
          fontSize: 12,
          formatter: (p: { name: string }) => p.name,
        },
        itemStyle: { shadowBlur: 6, shadowColor: 'rgba(37,99,235,0.35)' },
      },
    }],
  };
}

/** radar：xField=指标名，yFields=多个数值系列 */
function buildRadarChart(chart: ChartData): EChartsOption | null {
  const xField = getField(chart.spec, 'xField');
  const yFields = (chart.spec.yFields ?? []).filter((f): f is string => typeof f === 'string' && f.trim().length > 0);
  if (!hasColumn(chart.columns, xField) || yFields.length === 0) return null;
  // 防御：指标维度列不得与任何数值系列相同
  if (yFields.includes(xField)) return null;
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
  // 防御性校验：横轴、纵轴必须不同，且热力值列不得与二者相同
  if (!xField || !yField || !valueField) return null;
  if (xField === yField || valueField === xField || valueField === yField) return null;

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
  // 防御：分组字段不得与数值字段相同
  if (xField === valueField) return null;
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
  // 防御：横轴分类字段不得与任何数值系列相同
  if (specYFields.includes(xField)) return null;

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
