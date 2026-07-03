// chartSemantics.ts — 统一数据字段与数据集语义分析模块
//
// 本模块从 chartRegistry.ts 与 chartDescription.ts 复制现有判断逻辑，
// 最终将替代两处的分散实现。当前阶段仅新增，不修改旧文件。

import type { ChartSpec, ChartSuitability, RenderableChartType } from './types';

// ============================================================
// 类型定义
// ============================================================

export type Row = Record<string, unknown>;

/** 字段数据层分类 */
export type FieldDataKind = 'numeric' | 'temporal' | 'categorical' | 'unknown';

/** 字段语义角色 */
export type FieldSemanticRole =
  | 'entity'
  | 'region'
  | 'time'
  | 'identifier'
  | 'measure'
  | 'category'
  | 'unknown';

/** 指标语义分类 */
export type MeasureKind = 'additive' | 'non_additive' | 'unknown';

/** 数据集形态 */
export type DatasetShape =
  | 'empty'
  | 'single_value'
  | 'cross_section'
  | 'single_entity_time_series'
  | 'multi_entity_time_series'
  | 'relationship'
  | 'unknown';

/** 单字段画像 */
export interface FieldProfile {
  name: string;
  dataKind: FieldDataKind;
  semanticRole: FieldSemanticRole;
  measureKind: MeasureKind;
  nonNullCount: number;
  distinctCount: number;
}

/** 数据集完整画像 */
export interface DatasetProfile {
  numericFields: string[];
  temporalFields: string[];
  categoricalFields: string[];
  measureFields: string[];
  identifierFields: string[];
  entityField: string | null;
  regionField: string | null;
  fields: FieldProfile[];
  rowCount: number;
  shape: DatasetShape;
}

// ============================================================
// 基础工具函数（复制自 chartRegistry.ts）
// ============================================================

export function isNullValue(v: unknown): boolean {
  return v === null || v === undefined || v === '';
}

export function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

// ============================================================
// 字段类型判断
// ============================================================

export function isNumericField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(row => row[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  return values.every(v => toNumber(v) !== null);
}

// ---- 有序字符串识别（chartRegistry.ts + chartDescription.ts 共用） ----

/** 识别具有自然顺序的字符串：ISO 日期、中文日期/月份/季度、纯年份 */
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
  return values.every(
    v => typeof v === 'number' && Number.isInteger(v) && v >= 1900 && v <= 2100,
  );
}

/**
 * 严格时间字段判断（来自 chartRegistry.ts）：
 * 仅字符串日期/月份/季度 + 数字年份视为时间维度。
 * 排除 COD/BOD/温度/浓度等普通数值指标，避免被误当作时间横轴。
 */
export function isTemporalField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(r => r[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  // 全为有序字符串（日期/月份/季度/年份字符串）
  if (values.every(v => isOrderedStringValue(v))) return true;
  // 数字列：仅当字段名暗示年份或值域为合理四位年份时才视为时间维度
  if (values.every(v => typeof v === 'number')) {
    return isYearLikeFieldName(field) || isFourDigitYearValues(rows, field);
  }
  return false;
}

/**
 * 兼容有序字段判断（来自 chartDescription.ts）：
 * 在严格时间字段基础上，额外将纯数字字段也视为有序
 * （用于折线图首尾变化描述等需要有序横轴的场景）。
 */
export function isOrderedField(rows: Row[], field: string | null): boolean {
  if (!field) return false;
  const values = rows.map(r => r[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  return values.every(v => typeof v === 'number' || isOrderedStringValue(v));
}

// ============================================================
// 标识字段识别（仅按字段名匹配）
// ============================================================

const IDENTIFIER_PATTERNS = [/^id$/i, /_id$/i, /编号/, /编码/, /code/i];

function isIdentifierField(fieldName: string): boolean {
  return IDENTIFIER_PATTERNS.some(p => p.test(fieldName));
}

// ============================================================
// 实体与地区字段识别（复制自 chartRegistry.ts）
// ============================================================

/** 实体字段优先级关键词（仅匹配字段名） */
const ENTITY_PRIORITY: string[][] = [
  ['排口名称', '监测点名称', '站点名称', '断面名称', '水厂名称'],
  ['企业名称', '公司名称', '单位名称', '项目名称'],
  ['名称', 'name'],
  ['排口', '监测点', '站点', '断面', '企业', '公司', '水厂', '项目', 'site', 'station', 'company'],
];

/** 实体字段需排除的指标/标识词（字段名包含即排除） */
const ENTITY_METRIC_WORDS = [
  '数量', '个数', '总数', '次数', '金额', '浓度', '含量', '值',
  '比例', '占比', '平均', '均值', '总量', '排放量', '面积', '长度', '编号', '编码', 'id',
];

/** 地区字段名关键词 */
const REGION_KEYWORDS = ['区县', '地区', '区域', '行政区', '城市', 'city', 'region', 'district'];

/** 地区字段需排除的指标/标识词 */
const REGION_METRIC_WORDS = ['数量', '面积', '金额', '值', '率', '比例', '占比', '编号', '编码', 'id'];

/** 字段名是否包含任一排除词（大小写不敏感） */
function containsAnyWord(field: string, words: string[]): boolean {
  const lower = field.toLowerCase();
  return words.some(w => lower.includes(w.toLowerCase()));
}

/** 识别对象名称字段：仅选非数值分类列，排除指标/标识字段，按优先级返回第一个合法字段 */
export function findEntityNameField(columns: string[], rows: Row[]): string | null {
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

/** 识别地区字段：仅选非数值分类列，排除指标/标识字段，返回第一个合法字段 */
export function findRegionField(columns: string[], rows: Row[]): string | null {
  const numericCols = new Set(columns.filter(c => isNumericField(rows, c)));
  for (const col of columns) {
    if (numericCols.has(col)) continue;
    if (containsAnyWord(col, REGION_METRIC_WORDS)) continue;
    if (REGION_KEYWORDS.some(kw => col.toLowerCase().includes(kw.toLowerCase()))) return col;
  }
  return null;
}

/** 统计对象字段的不同值数量与单个对象最大出现次数（基于非空有效行） */
export function countEntityOccurrences(
  rows: Row[],
  entityField: string,
): { entityCount: number; maxOccur: number } {
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

// ============================================================
// 指标语义分类（复制自 chartDescription.ts 的关键词）
// ============================================================

/** 可加总指标：明确计数或累计量 */
const SUMMABLE_PATTERNS = [
  /数量/, /个数/, /总数/,
  /次数/, /人数/,
  /金额/, /收入/, /支出/,
  /排放总量/,
];

/** 不可加总指标：比例、浓度、指数、物理量等 */
const NON_SUMMABLE_PATTERNS = [
  /pH/i,
  /水位/, /高程/, /标高/,
  /温度/, /气温/, /水温/,
  /浓度/, /含量/, /密度/,
  /流量/, /排放量/, /用水量/, /供水量/, /发电量/,
  /速度/, /速率/, /流速/,
  /沉降/, /位移/, /变形/,
  /面积/, /长度/, /容积/, /库容/,
  /比例/, /占比/, /百分比/, /比率/,
  /平均值/, /均值/, /平均/,
  /指数/, /系数/, /等级/, /评分/, /得分/,
  /率$/,
];

/**
 * 指标语义分类：根据字段名（去掉聚合后缀）判定。
 * 先命中 NON_SUMMABLE → non_additive；
 * 否则命中 SUMMABLE → additive；
 * 否则 → unknown。
 */
export function classifyMeasureKind(fieldName: string): MeasureKind {
  const r = fieldName.replace(/_(avg|sum|count|min|max|total)$/, '');

  for (const p of NON_SUMMABLE_PATTERNS) {
    if (p.test(r)) return 'non_additive';
  }

  for (const p of SUMMABLE_PATTERNS) {
    if (p.test(r)) return 'additive';
  }

  return 'unknown';
}

/**
 * 判断是否可加总指标。
 * 等价于 classifyMeasureKind(field) === 'additive'。
 */
export function isSummable(yField: string): boolean {
  return classifyMeasureKind(yField) === 'additive';
}

// ============================================================
// 数据集形态判断
// ============================================================

/** 是否存在至少 2 个不同的非空时间点 */
function hasMultipleTimePoints(rows: Row[], temporalFields: string[]): boolean {
  for (const tf of temporalFields) {
    const distinct = new Set(
      rows.map(r => r[tf]).filter(v => !isNullValue(v)).map(String),
    );
    if (distinct.size >= 2) return true;
  }
  return false;
}

/** 根据实体、时间、指标字段推断数据集形态。
 *  cross_section 检测优先级：entityField → regionField → categoricalFields[0] */
function determineShape(
  columns: string[],
  rows: Row[],
  entityField: string | null,
  regionField: string | null,
  categoricalFields: string[],
  temporalFields: string[],
  measureFields: string[],
): DatasetShape {
  // 无列或无行 → empty
  if (columns.length === 0 || rows.length === 0) return 'empty';

  const hasMeasure = measureFields.length > 0;

  // 单行且有指标 → single_value
  if (rows.length === 1 && hasMeasure) return 'single_value';

  // 分类对比维度：entityField → regionField → categoricalFields[0]
  const comparisonField = entityField ?? regionField ?? (categoricalFields.length > 0 ? categoricalFields[0] : null);

  if (comparisonField && hasMeasure) {
    const { entityCount, maxOccur } = countEntityOccurrences(rows, comparisonField);
    // 至少 2 个不同值且每个值仅出现一次 → cross_section
    if (entityCount > 1 && maxOccur === 1) return 'cross_section';

    // 仅当对比维度是 entityField 且非 cross_section 时，继续检查时间序列形态
    if (comparisonField === entityField) {
      if (
        entityCount <= 1 &&
        hasMultipleTimePoints(rows, temporalFields) &&
        hasMeasure
      ) {
        return 'single_entity_time_series';
      }

      if (
        entityCount > 1 &&
        maxOccur > 1 &&
        hasMultipleTimePoints(rows, temporalFields) &&
        hasMeasure
      ) {
        return 'multi_entity_time_series';
      }
    }
  }

  // 无实体字段但存在多个时间点 → 单序列时间趋势
  if (!entityField && hasMultipleTimePoints(rows, temporalFields) && hasMeasure) {
    return 'single_entity_time_series';
  }

  // 至少两个指标 → relationship（散点/气泡图候选）
  if (measureFields.length >= 2) return 'relationship';

  return 'unknown';
}

// ============================================================
// 数据集分析（主入口）
// ============================================================

/**
 * 分析数据集，返回完整画像。
 * 入参 columns 为字段名列表、rows 为原始数据行（Record 数组）。
 */
export function analyzeDataset(columns: string[], rows: Row[]): DatasetProfile {
  // ── 阶段 1：每个字段独立计算标志（非互斥） ──

  const colFlags = new Map<string, {
    isNumeric: boolean;
    isTemporal: boolean;
    isIdentifier: boolean;
  }>();

  for (const col of columns) {
    colFlags.set(col, {
      isNumeric: isNumericField(rows, col),
      isTemporal: isTemporalField(rows, col),
      isIdentifier: isIdentifierField(col),
    });
  }

  // ── 阶段 2：构造字段列表（独立叠加） ──

  // numericFields：所有 isNumericField=true 的字段（含数字年份、数字 ID）
  const numericFields = columns.filter(c => colFlags.get(c)!.isNumeric);

  // temporalFields：所有 isTemporalField=true 的字段
  const temporalFields = columns.filter(c => colFlags.get(c)!.isTemporal);

  // identifierFields：仅按字段名规则识别
  const identifierFields = columns.filter(c => colFlags.get(c)!.isIdentifier);

  // measureFields：numericFields 排除 temporalFields 和 identifierFields
  const measureFields = numericFields.filter(
    c => !temporalFields.includes(c) && !identifierFields.includes(c),
  );

  // categoricalFields：非 numeric、非 temporal，且至少存在一个非空值
  const categoricalFields = columns.filter(c => {
    const f = colFlags.get(c)!;
    if (f.isNumeric || f.isTemporal) return false;
    return rows.some(r => !isNullValue(r[c]));
  });

  // ── 阶段 3：识别实体和地区字段 ──

  const entityField = findEntityNameField(columns, rows);
  const regionField = findRegionField(columns, rows);

  // ── 阶段 4：生成 FieldProfile ──

  const fields: FieldProfile[] = [];

  for (const col of columns) {
    const flags = colFlags.get(col)!;

    // dataKind 优先级：temporal > numeric > categorical > unknown
    let dataKind: FieldDataKind;
    if (flags.isTemporal) {
      dataKind = 'temporal';
    } else if (flags.isNumeric) {
      dataKind = 'numeric';
    } else if (categoricalFields.includes(col)) {
      dataKind = 'categorical';
    } else {
      dataKind = 'unknown';
    }

    // semanticRole 优先级：entity > region > time > identifier > measure > category > unknown
    let semanticRole: FieldSemanticRole;
    if (col === entityField) {
      semanticRole = 'entity';
    } else if (col === regionField) {
      semanticRole = 'region';
    } else if (flags.isTemporal) {
      semanticRole = 'time';
    } else if (flags.isIdentifier) {
      semanticRole = 'identifier';
    } else if (measureFields.includes(col)) {
      semanticRole = 'measure';
    } else if (categoricalFields.includes(col)) {
      semanticRole = 'category';
    } else {
      semanticRole = 'unknown';
    }

    // measureKind：仅 measureFields 使用 classifyMeasureKind，其他为 unknown
    const measureKind: MeasureKind = measureFields.includes(col)
      ? classifyMeasureKind(col)
      : 'unknown';

    // 非空行数 + 去重数
    const nonNull = rows.filter(r => !isNullValue(r[col]));
    const nonNullCount = nonNull.length;
    const distinctCount = new Set(nonNull.map(r => String(r[col]))).size;

    fields.push({
      name: col,
      dataKind,
      semanticRole,
      measureKind,
      nonNullCount,
      distinctCount,
    });
  }

  // ── 阶段 5：数据集形态 ──

  const shape = determineShape(columns, rows, entityField, regionField, categoricalFields, temporalFields, measureFields);

  return {
    numericFields,
    temporalFields,
    categoricalFields,
    measureFields,
    identifierFields,
    entityField,
    regionField,
    fields,
    rowCount: rows.length,
    shape,
  };
}

// ============================================================
// 核心图表语义计划
// ============================================================

export type ChartSemanticMode =
  | 'comparison'
  | 'trend'
  | 'part_to_whole'
  | 'relationship'
  | 'distribution'
  | 'kpi';

export type AggregationMode =
  | 'none'
  | 'sum'
  | 'average'
  | 'count'
  | 'distribution';

export interface ChartPlan {
  type: RenderableChartType;
  suitability: ChartSuitability;
  reasonCode: string;
  reason: string;
  spec: ChartSpec | null;
  semanticMode: ChartSemanticMode;
  aggregation: AggregationMode;
}

/** 根据数据集画像生成四种核心图表（bar/horizontal_bar/line/area）的语义计划。
 *  内部调用 analyzeDataset() 获取完整画像，按 DatasetShape 决定推荐优先级。
 *  preferredSpec 仅在字段符合语义约束时复用，不会改变 shape 判定。 */
export function resolveCoreChartPlans(
  columns: string[],
  rows: Row[],
  preferredSpec?: ChartSpec,
): ChartPlan[] {
  const profile = analyzeDataset(columns, rows);
  const { shape, entityField, regionField, categoricalFields, temporalFields, measureFields } = profile;

  /** 构造 ChartSpec（字段缺失时返回 null，title 从 preferredSpec 继承） */
  function makeSpec(
    type: RenderableChartType,
    xField: string | null,
    yField: string | null,
  ): ChartSpec | null {
    if (!xField || !yField) return null;
    return {
      type,
      title: preferredSpec?.title,
      xField,
      yFields: [yField],
      seriesField: null,
      sizeField: null,
      valueField: null,
    };
  }

  /** 尝试复用 preferredSpec.xField：仅当语义匹配时才采纳 */
  function resolveXField(chartType: RenderableChartType, defaultX: string | null): string | null {
    if (!preferredSpec?.xField || !columns.includes(preferredSpec.xField)) return defaultX;
    const px = preferredSpec.xField;
    // 趋势图（line/area）：xField 必须在 temporalFields
    if (chartType === 'line' || chartType === 'area') {
      return temporalFields.includes(px) ? px : defaultX;
    }
    // 对比图（bar/horizontal_bar）：xField 必须是 entityField、regionField 或分类字段
    if (px === entityField || px === regionField || categoricalFields.includes(px)) return px;
    return defaultX;
  }

  /** 尝试复用 preferredSpec.yFields[0]：仅当存在于 measureFields 时采纳 */
  function resolveYField(defaultY: string | null): string | null {
    if (!preferredSpec?.yFields?.[0]) return defaultY;
    const py = preferredSpec.yFields[0];
    return columns.includes(py) && measureFields.includes(py) ? py : defaultY;
  }

  /** 快捷构造 ChartPlan。
   *  不变量：非 unsupported 计划必须有有效 spec，否则自动降级为 unsupported。 */
  function plan(
    type: RenderableChartType,
    suitability: ChartSuitability,
    reasonCode: string,
    reason: string,
    xField: string | null,
    yField: string | null,
    semanticMode: ChartSemanticMode,
    aggregation: AggregationMode,
  ): ChartPlan {
    const spec = suitability !== 'unsupported' ? makeSpec(type, xField, yField) : null;
    if (suitability !== 'unsupported' && spec === null) {
      return {
        type,
        suitability: 'unsupported',
        reasonCode: 'invalid_plan_fields',
        reason: '缺少生成图表所需的有效字段',
        spec: null,
        semanticMode,
        aggregation,
      };
    }
    return {
      type,
      suitability,
      reasonCode,
      reason,
      spec,
      semanticMode,
      aggregation,
    };
  }

  const TYPES: RenderableChartType[] = ['bar', 'horizontal_bar', 'line', 'area'];
  const cmpY = measureFields[0] ?? null; // 默认数值字段
  const plans: ChartPlan[] = [];

  switch (shape) {

    // ── 横截面对比：bar/horizontal_bar 推荐，line 可显式切换，area 不支持 ──
    case 'cross_section': {
      const cmpX = resolveXField('bar', entityField ?? regionField ?? categoricalFields[0] ?? null);
      const y = resolveYField(cmpY);
      for (const t of TYPES) {
        if (t === 'bar' || t === 'horizontal_bar') {
          plans.push(plan(t, 'recommended', 'cross_section', '横截面对比数据，适合柱状图展示各实体指标', cmpX, y, 'comparison', 'none'));
        } else if (t === 'line') {
          plans.push(plan(t, 'allowed_explicit', 'cross_section_line', '横截面对比数据也可用折线图展示，需用户显式选择', cmpX, y, 'comparison', 'none'));
        } else {
          plans.push(plan(t, 'unsupported', 'cross_section_no_area', '横截面对比数据不适合面积图', null, null, 'comparison', 'none'));
        }
      }
      break;
    }

    // ── 单对象时间序列：line 推荐，其余允许显式切换 ──
    case 'single_entity_time_series': {
      const trendX = resolveXField('line', temporalFields[0] ?? null);
      const y = resolveYField(cmpY);
      for (const t of TYPES) {
        if (t === 'line') {
          plans.push(plan(t, 'recommended', 'single_entity_trend', '单对象时间序列，适合折线图展示趋势变化', trendX, y, 'trend', 'none'));
        } else {
          plans.push(plan(t, 'allowed_explicit', 'single_entity_secondary', '时间序列也可用此图表，但折线图更直观', trendX, y, 'trend', 'none'));
        }
      }
      break;
    }

    // ── 多对象时间序列：四种图表均不支持（渲染器尚无分系列能力） ──
    case 'multi_entity_time_series': {
      for (const t of TYPES) {
        plans.push(plan(t, 'unsupported', 'multi_entity_unsupported', '当前渲染器尚不支持分系列，禁止把多个实体连接为一条线', null, null, 'trend', 'none'));
      }
      break;
    }

    // ── 空数据：四种图表均不支持 ──
    case 'empty': {
      for (const t of TYPES) {
        plans.push(plan(t, 'unsupported', 'empty', '数据集为空', null, null, 'comparison', 'none'));
      }
      break;
    }

    // ── 单值：四种图表均不支持 ──
    case 'single_value': {
      for (const t of TYPES) {
        plans.push(plan(t, 'unsupported', 'single_value', '单值数据，不适合此类图表', null, null, 'kpi', 'none'));
      }
      break;
    }

    // ── 关系型：四种图表均不支持 ──
    case 'relationship': {
      for (const t of TYPES) {
        plans.push(plan(t, 'unsupported', 'relationship', '关系型数据，不适合此类图表', null, null, 'relationship', 'none'));
      }
      break;
    }

    // ── 未知形态：有分类 + 指标时 bar/horizontal_bar 允许显式切换 ──
    case 'unknown': {
      const hasCatAndMeasure = categoricalFields.length > 0 && measureFields.length > 0;
      if (hasCatAndMeasure) {
        const ux = resolveXField('bar', entityField ?? categoricalFields[0] ?? null);
        const y = resolveYField(cmpY);
        for (const t of TYPES) {
          if (t === 'bar' || t === 'horizontal_bar') {
            plans.push(plan(t, 'allowed_explicit', 'unknown_shape', '数据形态未知，可尝试柱状图', ux, y, 'comparison', 'none'));
          } else {
            plans.push(plan(t, 'unsupported', 'unknown_shape_no_trend', '数据形态未知，不适合折线图或面积图', null, null, 'comparison', 'none'));
          }
        }
      } else {
        for (const t of TYPES) {
          plans.push(plan(t, 'unsupported', 'insufficient_fields', '缺少必要的分类字段或指标字段', null, null, 'comparison', 'none'));
        }
      }
      break;
    }
  }

  return plans;
}
