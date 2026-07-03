// datasetProfilerV2.ts — V2 数据集画像分析模块
//
// 与 chartSemantics.ts 并行存在，不修改旧文件。
// 复用旧模块的算法逻辑，但独立实现以避免 NodeNext 模块解析问题。
// 阶段 A：仅新增，不接入运行时。

// ============================================================
// 基础类型
// ============================================================

export type Row = Record<string, unknown>;

/** 指标语义分类 */
export type MeasureKind = 'additive' | 'non_additive' | 'unknown';

/** 聚合状态 */
export type AggregationState = 'aggregated' | 'raw' | 'unknown';

/** 数据集结构原型（仅描述数据结构，不依赖 renderer 或 intent） */
export type DatasetArchetype =
  | 'empty'
  | 'single_value'
  | 'single_row_multi_measure'
  | 'multi_entity_temporal'
  | 'categorical_matrix'
  | 'numeric_relationship'
  | 'temporal_series'
  | 'categorical_series'
  | 'heterogeneous_metric_rows'
  | 'detail_rows'
  | 'unknown';

// ============================================================
// 基础工具函数（逻辑与 chartSemantics.ts 一致）
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

/** 识别具有自然顺序的字符串 */
function isOrderedStringValue(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  if (!isNaN(new Date(value).getTime())) return true;
  if (/^\d{4}年\d{1,2}月$/.test(value)) return true;
  if (/^\d{4}年第\d{1,2}季度$/.test(value)) return true;
  if (/^\d{1,2}月$/.test(value)) return true;
  if (/^Q[1-4]$/i.test(value)) return true;
  if (/^\d{4}$/.test(value)) return true;
  return false;
}

function isYearLikeFieldName(field: string): boolean {
  return /年|year/i.test(field);
}

function isFourDigitYearValues(rows: Row[], field: string): boolean {
  const values = rows.map(r => r[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  return values.every(
    v => typeof v === 'number' && Number.isInteger(v) && v >= 1900 && v <= 2100,
  );
}

export function isTemporalField(rows: Row[], field: string | null): field is string {
  if (!field) return false;
  const values = rows.map(r => r[field]).filter(v => !isNullValue(v));
  if (!values.length) return false;
  if (values.every(v => isOrderedStringValue(v))) return true;
  if (values.every(v => typeof v === 'number')) {
    return isYearLikeFieldName(field) || isFourDigitYearValues(rows, field);
  }
  return false;
}

// ============================================================
// 标识字段识别
// ============================================================

const IDENTIFIER_PATTERNS = [/^id$/i, /_id$/i, /编号/, /编码/, /code/i];

function isIdentifierField(fieldName: string): boolean {
  return IDENTIFIER_PATTERNS.some(p => p.test(fieldName));
}

// ============================================================
// 实体与地区字段识别
// ============================================================

const ENTITY_PRIORITY: string[][] = [
  ['排口名称', '监测点名称', '站点名称', '断面名称', '水厂名称'],
  ['企业名称', '公司名称', '单位名称', '项目名称'],
  ['名称', 'name'],
  ['排口', '监测点', '站点', '断面', '企业', '公司', '水厂', '项目', 'site', 'station', 'company'],
];

const ENTITY_METRIC_WORDS = [
  '数量', '个数', '总数', '次数', '金额', '浓度', '含量', '值',
  '比例', '占比', '平均', '均值', '总量', '排放量', '面积', '长度', '编号', '编码', 'id',
];

const REGION_KEYWORDS = ['区县', '地区', '区域', '行政区', '城市', 'city', 'region', 'district'];

const REGION_METRIC_WORDS = ['数量', '面积', '金额', '值', '率', '比例', '占比', '编号', '编码', 'id'];

function containsAnyWord(field: string, words: string[]): boolean {
  const lower = field.toLowerCase();
  return words.some(w => lower.includes(w.toLowerCase()));
}

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

export function findRegionField(columns: string[], rows: Row[]): string | null {
  const numericCols = new Set(columns.filter(c => isNumericField(rows, c)));
  for (const col of columns) {
    if (numericCols.has(col)) continue;
    if (containsAnyWord(col, REGION_METRIC_WORDS)) continue;
    if (REGION_KEYWORDS.some(kw => col.toLowerCase().includes(kw.toLowerCase()))) return col;
  }
  return null;
}

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
// 指标语义分类 V2
// ============================================================

/** 可加总指标：中文关键词 */
const SUMMABLE_PATTERNS = [
  /数量/, /个数/, /总数/,
  /次数/, /人数/,
  /金额/, /收入/, /支出/,
  /排放总量/,
];

/** 不可加总指标：中文关键词 */
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

/** classifyMeasureKind V2：先复用旧逻辑，再补充英文聚合名称 */
export function classifyMeasureKindV2(fieldName: string): MeasureKind {
  // 去掉常见聚合后缀
  const r = fieldName.replace(/_(avg|sum|count|min|max|total)$/, '');

  // Step 1: 旧中文规则
  for (const p of NON_SUMMABLE_PATTERNS) {
    if (p.test(r)) return 'non_additive';
  }
  for (const p of SUMMABLE_PATTERNS) {
    if (p.test(r)) return 'additive';
  }

  // Step 2: 补充英文聚合名称
  // additive: count, sum, total
  if (/^(count|sum|total)$/i.test(r)) return 'additive';
  if (/_(count|sum|total)$/i.test(r)) return 'additive';
  if (/^(.*_)?count$/i.test(r)) return 'additive';
  if (/^(.*_)?sum$/i.test(r)) return 'additive';
  if (/^(.*_)?total$/i.test(r)) return 'additive';

  // non_additive: avg, average, mean, rate, ratio, percent, percentage, min, max
  if (/^(avg|average|mean|rate|ratio|percent|percentage|min|max)$/i.test(r)) return 'non_additive';
  if (/_(avg|average|mean|rate|ratio|percent|percentage|min|max)$/i.test(r)) return 'non_additive';
  if (/^(.*_)?avg$/i.test(r)) return 'non_additive';
  if (/^(.*_)?average$/i.test(r)) return 'non_additive';
  if (/^(.*_)?mean$/i.test(r)) return 'non_additive';
  if (/^(.*_)?rate$/i.test(r)) return 'non_additive';
  if (/^(.*_)?ratio$/i.test(r)) return 'non_additive';
  if (/^(.*_)?percent$/i.test(r)) return 'non_additive';
  if (/^(.*_)?percentage$/i.test(r)) return 'non_additive';

  return 'unknown';
}

export function isSummableV2(yField: string): boolean {
  return classifyMeasureKindV2(yField) === 'additive';
}

// ============================================================
// 接口定义
// ============================================================

export interface DatasetTraitsV2 {
  // 维度相关
  dimensionFields: string[];
  primaryDimensionField: string | null;
  dimensionCardinality: number;
  /** 主维度值是否重复 */
  primaryDimensionHasDuplicates: boolean;
  /** 所有 dimensionFields 组成的完整维度元组是否重复 */
  duplicateDimensionKeys: boolean;

  // 矩阵相关
  uniqueDimensionPairRatio: number;

  // 聚合状态
  aggregationState: AggregationState;
  aggregationEvidence: string[];

  // 计数型
  measureCount: number;
  dimensionFieldCount: number;
  temporalFieldCount: number;
  entityFieldCount: number;
  numericFieldCount: number;
  rowCount: number;

  // 基数型
  categoryCardinality: number;
  entityCount: number;
  timePointCount: number;
  maxEntityOccurrence: number;

  // 布尔型
  hasNegativeValues: boolean;

  // 资格型
  multiSeriesEligible: boolean;
  multiSeriesCompleteness: number;
  groupedSamplesEligible: boolean;
  partToWholeEligible: boolean;
  matrixEligible: boolean;

  // 指标特征
  measureKinds: Record<string, MeasureKind>;
  measureTotals: Record<string, number>;

  // 不确定性
  detailConfidence: number;
  detailEvidence: string[];
  heterogeneousConfidence: number;
  heterogeneousEvidence: string[];
}

export interface DatasetProfileV2 {
  columns: string[];
  rowCount: number;

  // 字段分类
  numericFields: string[];
  temporalFields: string[];
  measureFields: string[];
  identifierFields: string[];
  entityField: string | null;
  regionField: string | null;

  // 结构原型
  archetype: DatasetArchetype;

  // 正交特征
  traits: DatasetTraitsV2;
}

// ============================================================
// 内部辅助：维度组合去重
// ============================================================

/** 计算完整维度组合的重复情况 */
function computeDimensionDuplicates(
  rows: Row[],
  dimensionFields: string[],
): { fullKeyHasDuplicates: boolean; uniquePairCount: number; totalPairs: number } {
  if (dimensionFields.length === 0) {
    return { fullKeyHasDuplicates: false, uniquePairCount: 0, totalPairs: 0 };
  }

  const seen = new Map<string, number>();
  for (const row of rows) {
    // 所有维度字段值非空时才算有效行
    if (dimensionFields.some(f => isNullValue(row[f]))) continue;
    const key = dimensionFields.map(f => String(row[f])).join('\x00');
    seen.set(key, (seen.get(key) ?? 0) + 1);
  }

  const fullKeyHasDuplicates = [...seen.values()].some(c => c > 1);
  return { fullKeyHasDuplicates, uniquePairCount: seen.size, totalPairs: [...seen.values()].reduce((s, c) => s + c, 0) };
}

// ============================================================
// 资格计算
// ============================================================

function computeGroupedSamplesEligible(
  rows: Row[],
  primaryDimensionField: string | null,
  measureFields: string[],
  measureKinds: Record<string, MeasureKind>,
  primaryDimensionHasDuplicates: boolean,
): boolean {
  // 前提：主维度有重复
  if (!primaryDimensionHasDuplicates) return false;
  // 前提：恰好 1 个 measure
  if (measureFields.length !== 1) return false;
  // 前提：主维度基数 >= 2
  if (!primaryDimensionField) return false;

  const distinctDims = new Set(
    rows.map(r => r[primaryDimensionField]).filter(v => !isNullValue(v)).map(String),
  );
  if (distinctDims.size < 2) return false;

  // 关键：measure 必须是 non_additive，不是可加型 count/sum
  const m = measureFields[0];
  const kind = measureKinds[m] ?? 'unknown';
  if (kind !== 'non_additive') return false;

  // 每组至少 2 个有效数值样本
  const groupSizes = new Map<string, number>();
  for (const row of rows) {
    const dimVal = row[primaryDimensionField];
    const measureVal = row[m];
    if (isNullValue(dimVal) || isNullValue(measureVal)) continue;
    if (toNumber(measureVal) === null) continue;
    const key = String(dimVal);
    groupSizes.set(key, (groupSizes.get(key) ?? 0) + 1);
  }

  if (groupSizes.size < 2) return false;
  const minGroupSize = Math.min(...groupSizes.values());
  if (minGroupSize < 2) return false;

  return true;
}

function computePartToWholeEligible(
  measureFields: string[],
  measureKinds: Record<string, MeasureKind>,
  measureTotals: Record<string, number>,
  hasNegativeValues: boolean,
  dimensionCardinality: number,
): boolean {
  if (measureFields.length !== 1) return false;
  const m = measureFields[0];
  const kind = measureKinds[m] ?? 'unknown';
  // 可加或 unknown（保守允许）
  if (kind === 'non_additive') return false;
  if (hasNegativeValues) return false;
  const total = measureTotals[m] ?? 0;
  if (total <= 0) return false;
  if (dimensionCardinality < 2) return false;
  if (dimensionCardinality > 12) return false;
  return true;
}

function computeMatrixEligible(
  dimensionFields: string[],
  measureFields: string[],
  uniqueDimensionPairRatio: number,
): boolean {
  if (dimensionFields.length < 2) return false;
  if (measureFields.length !== 1) return false;
  return uniqueDimensionPairRatio >= 0.3;
}

function computeMultiSeriesCompleteness(
  rows: Row[],
  entityField: string | null,
  temporalFields: string[],
): number {
  if (!entityField || temporalFields.length === 0) return 0;

  const tf = temporalFields[0];
  const globalTimePoints = new Set(
    rows.map(r => r[tf]).filter(v => !isNullValue(v)).map(String),
  );
  if (globalTimePoints.size === 0) return 0;

  const entityTimes = new Map<string, Set<string>>();
  for (const row of rows) {
    const e = row[entityField];
    const t = row[tf];
    if (isNullValue(e) || isNullValue(t)) continue;
    const eKey = String(e);
    if (!entityTimes.has(eKey)) entityTimes.set(eKey, new Set());
    entityTimes.get(eKey)!.add(String(t));
  }

  if (entityTimes.size === 0) return 0;

  let totalRatio = 0;
  for (const [, ts] of entityTimes) {
    totalRatio += ts.size / globalTimePoints.size;
  }
  return totalRatio / entityTimes.size;
}

// ============================================================
// aggregationState 判定
// ============================================================

function determineAggregationState(
  columns: string[],
  rows: Row[],
  identifierFields: string[],
  measureFields: string[],
  fullKeyHasDuplicates: boolean,
): { state: AggregationState; evidence: string[] } {
  const evidence: string[] = [];
  let scoreAggregated = 0;
  let scoreRaw = 0;

  // 证据 A：完整维度组合无重复 → +2 aggregated
  if (!fullKeyHasDuplicates) {
    scoreAggregated += 2;
    evidence.push('完整维度组合无重复（聚合特征）');
  } else {
    scoreRaw += 1;
    evidence.push('完整维度组合存在重复');
  }

  // 证据 B：所有 measureField 名含聚合后缀 → +2 aggregated
  const allAggSuffix = measureFields.length > 0 && measureFields.every(
    f => /_(sum|count|avg|min|max|total)$/.test(f),
  );
  if (allAggSuffix) {
    scoreAggregated += 2;
    evidence.push('所有指标字段名含聚合后缀');
  }

  // 证据 B2：英文聚合后缀
  const allEngAggSuffix = measureFields.length > 0 && measureFields.every(
    f => /_(sum|count|avg|average|mean|min|max|total)$/i.test(f),
  );
  if (allEngAggSuffix && !allAggSuffix) {
    scoreAggregated += 1;
    evidence.push('指标字段名含英文聚合后缀');
  }

  // 证据 C：存在 identifierField → +2 raw
  if (identifierFields.length > 0) {
    scoreRaw += 2;
    evidence.push(`存在标识字段: ${identifierFields.join(', ')}`);
  }

  // 证据 D：rowCount > 50 且 measureCount <= 1 → +1 raw
  if (rows.length > 50 && measureFields.length <= 1) {
    scoreRaw += 1;
    evidence.push(`行数较多(${rows.length})且指标少，可能为明细`);
  }

  // 证据 E：字段名含明细/记录关键词 → +1 raw
  const detailKeywords = ['明细', '记录', '列表', '原始', 'detail', 'raw', 'log'];
  const hasDetailKeyword = columns.some(c =>
    detailKeywords.some(kw => c.toLowerCase().includes(kw)),
  );
  if (hasDetailKeyword) {
    scoreRaw += 1;
    evidence.push('字段名含明细/记录关键词');
  }

  // 注意：entityField 存在不作为 raw 证据

  if (scoreAggregated > scoreRaw) {
    return { state: 'aggregated', evidence };
  }
  if (scoreRaw > scoreAggregated) {
    return { state: 'raw', evidence };
  }
  return { state: 'unknown', evidence };
}

// ============================================================
// detailConfidence
// ============================================================

function computeDetailConfidence(
  rows: Row[],
  identifierFields: string[],
  aggregationState: AggregationState,
  dimensionFieldCount: number,
  duplicateDimensionKeys: boolean,
  groupedSamplesEligible: boolean,
): { confidence: number; evidence: string[] } {
  let score = 0;
  const evidence: string[] = [];

  // +0.3: 存在标识字段
  if (identifierFields.length > 0) {
    score += 0.3;
    evidence.push(`存在标识字段: ${identifierFields.join(', ')}`);
  }

  // +0.3: aggregationState !== 'aggregated'
  if (aggregationState !== 'aggregated') {
    score += 0.3;
    evidence.push(`聚合状态非 aggregated (${aggregationState})`);
  }

  // +0.2: 无维度字段
  if (dimensionFieldCount === 0) {
    score += 0.2;
    evidence.push('无维度字段');
  }

  // +0.2: 有重复维度键且行数>=20
  if (duplicateDimensionKeys && rows.length >= 20) {
    score += 0.2;
    evidence.push(`行数较多(${rows.length})且维度键重复`);
  }

  // +0.2: 有明确分组样本结构（非可加指标的重复分组）
  if (groupedSamplesEligible) {
    score += 0.2;
    evidence.push('明确分组样本结构（非可加指标 + 重复分组 + 每组多样本）');
  }

  // -0.3: aggregationState === 'aggregated' 且无重复维度键
  if (aggregationState === 'aggregated' && !duplicateDimensionKeys) {
    score -= 0.3;
    evidence.push('存在明确聚合结构');
  }

  return {
    confidence: Math.max(0, Math.min(1, score)),
    evidence,
  };
}

// ============================================================
// heterogeneousConfidence（保守判定）
// ============================================================

/** 异构汇总：维度值匹配统计口径关键词 */
const STAT_KEYWORDS = [
  '总数', '数量', '个数', '记录数',
  '平均', '均值', '平均每个',
  '最大', '最大值', '最小', '最小值',
  '合计', '总计',
  '中位数',
  '比例', '占比',
];

/** 异构汇总：维度字段名匹配关键词 */
const HETEROGENEOUS_DIM_NAME_KEYWORDS = [
  '指标', '统计项', '统计指标', '统计口径', 'metric', 'measure',
];

function computeHeterogeneousConfidence(
  rows: Row[],
  primaryDimensionField: string | null,
  dimensionFields: string[],
  measureFields: string[],
  dimensionCardinality: number,
): { confidence: number; evidence: string[] } {
  // 前提：行数 2～10
  if (rows.length < 2 || rows.length > 10) {
    return { confidence: 0, evidence: ['行数不在 2～10 范围'] };
  }

  // 前提：恰好 1 个 measure
  if (measureFields.length !== 1) {
    return { confidence: 0, evidence: [`measure 数量(${measureFields.length})不为 1`] };
  }

  // 前提：恰好 1 个主要维度
  if (dimensionFields.length !== 1) {
    return { confidence: 0, evidence: [`维度数量(${dimensionFields.length})不为 1`] };
  }

  let score = 0;
  const evidence: string[] = [];
  const dimField = dimensionFields[0];

  // 证据 A：维度字段名匹配关键词 → +0.4
  if (HETEROGENEOUS_DIM_NAME_KEYWORDS.some(kw => dimField.toLowerCase().includes(kw.toLowerCase()))) {
    score += 0.4;
    evidence.push(`维度字段名"${dimField}"匹配异构关键词`);
  }

  // 证据 B：维度值匹配统计口径关键词
  if (primaryDimensionField) {
    const dimValues = rows
      .map(r => String(r[primaryDimensionField] ?? ''))
      .filter(v => v.length > 0);

    const statMatches = dimValues.filter(v =>
      STAT_KEYWORDS.some(kw => v.includes(kw)),
    );

    if (statMatches.length >= 2) {
      score += 0.3;
      evidence.push(`至少 2 个维度值匹配统计口径: ${statMatches.slice(0, 3).join(', ')}`);
    } else if (statMatches.length === 1 && dimValues.length <= 4) {
      // 单个匹配但行数少
      score += 0.15;
      evidence.push(`1 个维度值匹配统计口径: ${statMatches[0]}`);
    }
  }

  // 证据 C：每个维度值仅一行（每行语义可能不同）
  if (dimensionCardinality === rows.length) {
    score += 0.2;
    evidence.push('每个维度值仅一行，可能每行语义不同');
  }

  // 反对证据：维度值看起来像同类（区县名、月份、站点名）
  if (primaryDimensionField) {
    const dimValues = rows
      .map(r => String(r[primaryDimensionField] ?? ''))
      .filter(v => v.length > 0);

    const homogeneousSuffixes = ['区', '县', '市', '省', '月', '季度', '年', '站', '厂', '公司', '企业'];
    const allHomogeneous = dimValues.length > 0 && dimValues.every(v =>
      homogeneousSuffixes.some(s => v.endsWith(s)),
    );
    if (allHomogeneous) {
      score -= 0.4;
      evidence.push('维度值属同一类别（如区县/月份/站点），非异构汇总');
    }

    // 反对证据：维度值看起来像普通分类（产品名称、项目名称等）
    const entityLike = dimValues.length > 0 && dimValues.every(v => v.length >= 3 && v.length <= 20);
    const statLike = dimValues.some(v => STAT_KEYWORDS.some(kw => v.includes(kw)));
    if (entityLike && !statLike && dimValues.length > 3) {
      score -= 0.2;
      evidence.push('维度值像普通分类名称，非统计口径');
    }
  }

  return {
    confidence: Math.max(0, Math.min(1, score)),
    evidence,
  };
}

// ============================================================
// Archetype 判定
// ============================================================

function determineArchetype(
  columns: string[],
  rows: Row[],
  traits: DatasetTraitsV2,
  entityField: string | null,
  measureFields: string[],
  dimensionFields: string[],
  temporalFields: string[],
): DatasetArchetype {
  // (0) empty
  if (columns.length === 0 || rows.length === 0) return 'empty';

  // (1) single_value
  if (rows.length === 1 && measureFields.length === 1) return 'single_value';

  // (2) single_row_multi_measure
  if (rows.length === 1 && measureFields.length >= 2) return 'single_row_multi_measure';

  // (3) heterogeneous_metric_rows
  if (traits.heterogeneousConfidence >= 0.6) return 'heterogeneous_metric_rows';

  // (4) multi_entity_temporal
  if (
    entityField !== null &&
    temporalFields.length >= 1 &&
    traits.entityCount >= 2
  ) {
    return 'multi_entity_temporal';
  }

  // (5) categorical_matrix
  if (
    dimensionFields.length >= 2 &&
    measureFields.length === 1 &&
    traits.matrixEligible
  ) {
    return 'categorical_matrix';
  }

  // (6) numeric_relationship
  if (
    dimensionFields.length === 0 &&
    temporalFields.length === 0 &&
    measureFields.length >= 2
  ) {
    return 'numeric_relationship';
  }

  // (7) temporal_series
  if (
    temporalFields.length >= 1 &&
    traits.timePointCount >= 2 &&
    measureFields.length >= 1
  ) {
    return 'temporal_series';
  }

  // (8) categorical_series
  if (
    dimensionFields.length >= 1 &&
    measureFields.length >= 1 &&
    !traits.duplicateDimensionKeys
  ) {
    return 'categorical_series';
  }

  // (9) detail_rows
  if (traits.detailConfidence >= 0.5) return 'detail_rows';

  // (10) unknown
  return 'unknown';
}

// ============================================================
// 主入口：analyzeDatasetV2
// ============================================================

export function analyzeDatasetV2(
  columns: string[],
  rows: Row[],
  fieldLabels?: Record<string, string>, // 预留：未来用于提高实体/地区识别准确率
): DatasetProfileV2 {
  // 预留：fieldLabels 未来用于提高实体/地区识别准确率
  void fieldLabels;

  // ── 阶段 1：字段分类 ──
  const numericFields = columns.filter(c => isNumericField(rows, c));
  const temporalFields = columns.filter(c => isTemporalField(rows, c));
  const identifierFields = columns.filter(c => isIdentifierField(c));

  // measureFields：numericFields 中排除 temporalFields 和 identifierFields
  const measureFields = numericFields.filter(
    c => !temporalFields.includes(c) && !identifierFields.includes(c),
  );

  // dimensionFields：非 measure、非 identifier
  const dimensionFields = columns.filter(
    c => !measureFields.includes(c) && !identifierFields.includes(c),
  );

  // ── 阶段 2：实体和地区识别 ──
  const entityField = findEntityNameField(columns, rows);
  const regionField = findRegionField(columns, rows);

  // ── 阶段 3：主维度 ──
  const primaryDimensionField: string | null =
    entityField ?? regionField ?? (dimensionFields.length > 0 ? dimensionFields[0] : null);

  // ── 阶段 4：维度重复计算 ──
  const primaryDimOccurrence = primaryDimensionField
    ? countEntityOccurrences(rows, primaryDimensionField)
    : { entityCount: 0, maxOccur: 0 };

  const primaryDimensionHasDuplicates = primaryDimOccurrence.maxOccur > 1;
  const dimensionCardinality = primaryDimOccurrence.entityCount;

  const dimDupResult = computeDimensionDuplicates(rows, dimensionFields);
  const duplicateDimensionKeys = dimDupResult.fullKeyHasDuplicates;

  // ── 阶段 5：时间点和实体计数 ──
  let timePointCount = 0;
  if (temporalFields.length > 0) {
    const tf = temporalFields[0];
    timePointCount = new Set(
      rows.map(r => r[tf]).filter(v => !isNullValue(v)).map(String),
    ).size;
  }

  const entityOccurrence = entityField
    ? countEntityOccurrences(rows, entityField)
    : { entityCount: 0, maxOccur: 0 };

  // ── 阶段 6：指标特征 ──
  const measureKinds: Record<string, MeasureKind> = {};
  const measureTotals: Record<string, number> = {};
  let hasNegativeValues = false;

  for (const m of measureFields) {
    measureKinds[m] = classifyMeasureKindV2(m);
    let total = 0;
    for (const row of rows) {
      const v = toNumber(row[m]);
      if (v !== null) {
        if (v < 0) hasNegativeValues = true;
        total += v;
      }
    }
    measureTotals[m] = total;
  }

  // ── 阶段 7：uniqueDimensionPairRatio ──
  let uniqueDimensionPairRatio = 0;
  if (dimensionFields.length >= 2) {
    // 计算两个主要维度的基数乘积
    const dim1 = dimensionFields[0];
    const dim2 = dimensionFields[1];
    const card1 = new Set(rows.map(r => String(r[dim1] ?? ''))).size;
    const card2 = new Set(rows.map(r => String(r[dim2] ?? ''))).size;
    const product = card1 * card2;
    if (product > 0) {
      uniqueDimensionPairRatio = dimDupResult.uniquePairCount / product;
    }
  }

  // ── 阶段 8：资格计算 ──
  const groupedSamplesEligible = computeGroupedSamplesEligible(
    rows, primaryDimensionField, measureFields, measureKinds, primaryDimensionHasDuplicates,
  );

  const partToWholeEligible = computePartToWholeEligible(
    measureFields, measureKinds, measureTotals, hasNegativeValues, dimensionCardinality,
  );

  const matrixEligible = computeMatrixEligible(
    dimensionFields, measureFields, uniqueDimensionPairRatio,
  );

  const multiSeriesEligible =
    entityField !== null &&
    entityOccurrence.entityCount >= 2 &&
    temporalFields.length >= 1 &&
    timePointCount >= 2 &&
    entityOccurrence.maxOccur >= 2;

  const multiSeriesCompleteness = computeMultiSeriesCompleteness(rows, entityField, temporalFields);

  // ── 阶段 9：aggregationState ──
  const aggResult = determineAggregationState(
    columns, rows, identifierFields, measureFields, duplicateDimensionKeys,
  );

  // ── 阶段 10：置信度 ──
  const detailResult = computeDetailConfidence(
    rows, identifierFields, aggResult.state, dimensionFields.length,
    duplicateDimensionKeys, groupedSamplesEligible,
  );

  const heteroResult = computeHeterogeneousConfidence(
    rows, primaryDimensionField, dimensionFields, measureFields, dimensionCardinality,
  );

  // ── 阶段 11：组装 traits ──
  const traits: DatasetTraitsV2 = {
    dimensionFields,
    primaryDimensionField,
    dimensionCardinality,
    primaryDimensionHasDuplicates,
    duplicateDimensionKeys,
    uniqueDimensionPairRatio,
    aggregationState: aggResult.state,
    aggregationEvidence: aggResult.evidence,
    measureCount: measureFields.length,
    dimensionFieldCount: dimensionFields.length,
    temporalFieldCount: temporalFields.length,
    entityFieldCount: entityField ? 1 : 0,
    numericFieldCount: numericFields.length,
    rowCount: rows.length,
    categoryCardinality: dimensionCardinality,
    entityCount: entityOccurrence.entityCount,
    timePointCount,
    maxEntityOccurrence: entityOccurrence.maxOccur,
    hasNegativeValues,
    multiSeriesEligible,
    multiSeriesCompleteness,
    groupedSamplesEligible,
    partToWholeEligible,
    matrixEligible,
    measureKinds,
    measureTotals,
    detailConfidence: detailResult.confidence,
    detailEvidence: detailResult.evidence,
    heterogeneousConfidence: heteroResult.confidence,
    heterogeneousEvidence: heteroResult.evidence,
  };

  // ── 阶段 12：archetype ──
  const archetype = determineArchetype(
    columns, rows, traits, entityField, measureFields, dimensionFields, temporalFields,
  );

  return {
    columns,
    rowCount: rows.length,
    numericFields,
    temporalFields,
    measureFields,
    identifierFields,
    entityField,
    regionField,
    archetype,
    traits,
  };
}
