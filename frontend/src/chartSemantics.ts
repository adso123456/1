// chartSemantics.ts — 统一数据字段与数据集语义分析模块
//
// 本模块从 chartRegistry.ts 与 chartDescription.ts 复制现有判断逻辑，
// 最终将替代两处的分散实现。当前阶段仅新增，不修改旧文件。

// ============================================================
// 类型定义
// ============================================================

export type Row = Record<string, unknown>;

/** 字段数据层分类 */
export type FieldDataKind = 'numeric' | 'temporal' | 'categorical' | 'measure' | 'identifier';

/** 字段语义角色 */
export type FieldSemanticRole = 'dimension' | 'measure' | 'identifier' | 'temporal' | 'unknown';

/** 指标语义分类 */
export type MeasureKind =
  | 'summable_count'
  | 'summable_amount'
  | 'concentration'
  | 'rate'
  | 'ratio'
  | 'physical_quantity'
  | 'score'
  | 'generic';

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
  kind: FieldDataKind;
  role: FieldSemanticRole;
  nullCount: number;
  distinctCount: number;
  measureKind?: MeasureKind;
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
// 标识字段识别
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
// 指标语义分类（复制自 chartDescription.ts）
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
 * 指标语义分类：根据字段名（去掉聚合后缀）判定指标类型。
 * 先匹配不可加总模式（更具体），再匹配可加总模式，均不匹配则返回 generic。
 */
export function classifyMeasureKind(fieldName: string): MeasureKind {
  const r = fieldName.replace(/_(avg|sum|count|min|max|total)$/, '');

  // 不可加总模式 → 子分类
  for (const p of NON_SUMMABLE_PATTERNS) {
    if (p.test(r)) {
      if (/浓度|含量|密度/.test(r)) return 'concentration';
      if (/率$/.test(r)) return 'rate';
      if (/比例|占比|百分比|比率/.test(r)) return 'ratio';
      if (/pH/i.test(r)) return 'physical_quantity';
      if (/温度|气温|水温/.test(r)) return 'physical_quantity';
      if (/速度|速率|流速/.test(r)) return 'physical_quantity';
      if (/水位|高程|标高/.test(r)) return 'physical_quantity';
      if (/流量|排放量|用水量|供水量|发电量/.test(r)) return 'physical_quantity';
      if (/沉降|位移|变形/.test(r)) return 'physical_quantity';
      if (/面积|长度|容积|库容/.test(r)) return 'physical_quantity';
      if (/指数|系数|等级|评分|得分/.test(r)) return 'score';
      if (/平均值|均值|平均/.test(r)) return 'score';
      return 'generic';
    }
  }

  // 可加总模式 → 子分类
  for (const p of SUMMABLE_PATTERNS) {
    if (p.test(r)) {
      if (/金额|收入|支出/.test(r)) return 'summable_amount';
      return 'summable_count';
    }
  }

  return 'generic';
}

/**
 * 判断是否可加总指标（来自 chartDescription.ts）。
 * 默认不可加，仅明确匹配 SUMMABLE 且不匹配 NON_SUMMABLE 时返回 true。
 */
export function isSummable(yField: string): boolean {
  const r = yField.replace(/_(avg|sum|count|min|max|total)$/, '');
  for (const p of NON_SUMMABLE_PATTERNS) {
    if (p.test(r)) return false;
  }
  for (const p of SUMMABLE_PATTERNS) {
    if (p.test(r)) return true;
  }
  return false;
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

/** 根据实体、时间、指标字段推断数据集形态 */
function determineShape(
  rows: Row[],
  entityField: string | null,
  temporalFields: string[],
  measureFields: string[],
): DatasetShape {
  // 无数据 → empty
  if (rows.length === 0) return 'empty';

  const hasMeasure = measureFields.length > 0;

  // 单行且有指标 → single_value
  if (rows.length === 1 && hasMeasure) return 'single_value';

  if (entityField) {
    const { entityCount, maxOccur } = countEntityOccurrences(rows, entityField);

    // 多实体各一条且有指标 → cross_section
    if (entityCount > 1 && maxOccur === 1 && hasMeasure) return 'cross_section';

    // 单实体，存在多个时间点 → single_entity_time_series
    if (
      entityCount <= 1 &&
      temporalFields.length > 0 &&
      hasMultipleTimePoints(rows, temporalFields)
    ) {
      return 'single_entity_time_series';
    }

    // 多实体且实体有重复记录，并存在时间字段 → multi_entity_time_series
    if (entityCount > 1 && maxOccur > 1 && temporalFields.length > 0) {
      return 'multi_entity_time_series';
    }
  } else {
    // 无实体字段，存在多个时间点且有指标 → single_entity_time_series
    if (
      temporalFields.length > 0 &&
      hasMultipleTimePoints(rows, temporalFields) &&
      hasMeasure
    ) {
      return 'single_entity_time_series';
    }
  }

  // 至少两个数值指标 → relationship（散点/气泡图候选）
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
  const numericFields: string[] = [];
  const temporalFields: string[] = [];
  const categoricalFields: string[] = [];
  const measureFields: string[] = [];
  const identifierFields: string[] = [];
  const fields: FieldProfile[] = [];

  for (const col of columns) {
    const isNumeric = isNumericField(rows, col);
    const isTemporal = isTemporalField(rows, col);
    const isIdent = isIdentifierField(col);

    let kind: FieldDataKind;
    let role: FieldSemanticRole;

    if (isIdent) {
      kind = 'identifier';
      role = 'identifier';
      identifierFields.push(col);
    } else if (isTemporal) {
      kind = 'temporal';
      role = 'temporal';
      temporalFields.push(col);
    } else if (isNumeric) {
      kind = 'numeric';
      role = 'measure';
      numericFields.push(col);
      measureFields.push(col);
    } else {
      kind = 'categorical';
      role = 'dimension';
      categoricalFields.push(col);
    }

    // 统计空值与去重数
    const nonNull = rows.filter(r => !isNullValue(r[col]));
    const nullCount = rows.length - nonNull.length;
    const distinctCount = new Set(nonNull.map(r => String(r[col]))).size;

    // 仅数值字段计算指标语义分类
    const measureKind = isNumeric ? classifyMeasureKind(col) : undefined;

    fields.push({
      name: col,
      kind,
      role,
      nullCount,
      distinctCount,
      measureKind,
    });
  }

  const entityField = findEntityNameField(columns, rows);
  const regionField = findRegionField(columns, rows);
  const shape = determineShape(rows, entityField, temporalFields, measureFields);

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
