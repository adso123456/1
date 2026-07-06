// chartCapabilityV2.ts — V2 图表能力契约（最小试点：bar / line / boxplot）
//
// 阶段 B：仅新增契约定义，不接入运行时。

import type { DatasetArchetype } from './datasetProfilerV2.js';
import type { DatasetTraitsV2 } from './datasetProfilerV2.js';
import type { RenderableChartType, ChartSuitability } from './types.js';

// ============================================================
// 语义模式
// ============================================================

export type ChartSemanticMode =
  | 'comparison'
  | 'trend'
  | 'part_to_whole'
  | 'relationship'
  | 'distribution'
  | 'profile'
  | 'kpi';

// ============================================================
// 适用性等级
// ============================================================

/** variant 级别的适用性（不含 unsupported） */
export type SupportedSuitability = Exclude<ChartSuitability, 'unsupported'>;

/** 每个 archetype → 基础适用性映射 */
export type ArchetypeSuitability = Partial<Record<DatasetArchetype, SupportedSuitability>>;

// ============================================================
// 类型安全的 TraitRequirement
// ============================================================

/** 从 DatasetTraitsV2 中提取 boolean 类型的 trait 名称 */
type BooleanTraitName = {
  [K in keyof DatasetTraitsV2]: DatasetTraitsV2[K] extends boolean ? K : never;
}[keyof DatasetTraitsV2];

/** 从 DatasetTraitsV2 中提取 number 类型的 trait 名称 */
type NumericTraitName = {
  [K in keyof DatasetTraitsV2]: DatasetTraitsV2[K] extends number ? K : never;
}[keyof DatasetTraitsV2];

/**
 * 从 DatasetTraitsV2 中提取 string 字面量联合类型的 trait 名称。
 * 仅匹配值类型为字面量联合（如 AggregationState）的字段；
 * string | null、string[]、Record 等不满足 extends string，故不会被选中。
 * 当前仅 aggregationState 入选。
 */
type StringTraitName = {
  [K in keyof DatasetTraitsV2]: DatasetTraitsV2[K] extends string ? K : never;
}[keyof DatasetTraitsV2];

/**
 * 类型安全的 trait 要求。
 *
 * boolean trait — 严格互斥，必须恰好使用一种：
 *   - equals: boolean
 *   - required: true
 *   - forbidden: true
 *
 * numeric trait — 必须至少包含 equals / min / max 之一。
 *
 * string trait — 仅支持字面量相等（如 aggregationState === 'aggregated'）。
 */
export type TraitRequirement =
  // ── boolean（严格互斥：恰好一种） ──
  | { trait: BooleanTraitName; equals: boolean; required?: never; forbidden?: never }
  | { trait: BooleanTraitName; required: true; equals?: never; forbidden?: never }
  | { trait: BooleanTraitName; forbidden: true; equals?: never; required?: never }
  // ── numeric（必须至少包含 equals / min / max 之一） ──
  | { trait: NumericTraitName; equals: number }
  | { trait: NumericTraitName; min: number | null; max?: number | null }
  | { trait: NumericTraitName; max: number | null; min?: number | null }
  // ── string（字面量相等，仅 aggregationState 等）──
  | { trait: StringTraitName; equals: string };

// ============================================================
// 字段选择器
// ============================================================

export type ScalarFieldSelector =
  | { source: 'entityField' }
  | { source: 'regionField' }
  | { source: 'primaryDimensionField' }
  | { source: 'dimensionField'; index: number }
  | { source: 'temporalField'; index: number }
  | { source: 'measureField'; index: number }
  | { source: 'additiveMeasureField'; index: number }
  | { source: 'nonAdditiveMeasureField'; index: number }
  | { source: 'numericField'; exclude?: string[] }
  // ── preferredSpec 字段 ──
  | { source: 'preferredXField' }
  | { source: 'preferredYField'; index: number }
  | { source: 'preferredSeriesField' }
  | { source: 'preferredSizeField' }
  | { source: 'preferredValueField' };

export type MultiFieldSelector =
  | { source: 'measureFields'; maxCount?: number }
  | { source: 'measureFieldsAfter'; afterIndex: number; maxCount?: number }
  | { source: 'additiveMeasureFields'; maxCount?: number }
  | { source: 'nonAdditiveMeasureFields'; maxCount?: number }
  | { source: 'dimensionFields'; maxCount?: number }
  | { source: 'dimensionFieldsAfter'; afterIndex: number; maxCount?: number }
  | { source: 'temporalFields'; maxCount?: number }
  // ── preferredSpec 字段 ──
  | { source: 'preferredYFields' };

/** 字段映射——所有字段均为可选 */
export interface VariantFieldMapping {
  xField?: ScalarFieldSelector;
  yFields?: MultiFieldSelector;
  valueField?: ScalarFieldSelector;
  sizeField?: ScalarFieldSelector;
  seriesField?: ScalarFieldSelector;
}

// ============================================================
// 数据转换计划
// ============================================================

export type DataTransformPlan =
  | 'none'
  | 'group_by_sum'
  | 'group_by_average'
  | 'percent_of_total'
  | 'boxplot_summary'
  | 'matrix_aggregate';

// ============================================================
// Renderer 能力门槛
// ============================================================

export interface RendererRequirement {
  capability: string;
  description: string;
  currentlySupported: boolean;
}

// ============================================================
// ChartCapabilityVariant & ChartCapability
// ============================================================

export interface ChartCapabilityVariant {
  id: string;
  archetypeSuitability: ArchetypeSuitability;
  traitRequirements: readonly TraitRequirement[];
  semanticMode: ChartSemanticMode;
  maxSuitability: SupportedSuitability;
  fieldMapping: VariantFieldMapping;
  transform: DataTransformPlan;
  rendererRequirements: readonly RendererRequirement[];
  unsupportedReasonCode: string;
}

export interface ChartCapability {
  type: RenderableChartType;
  label: string;
  variants: readonly ChartCapabilityVariant[];
}

// ============================================================
// 试点能力清单
// ============================================================

export const PILOT_CAPABILITIES_V2 = [
  // ── bar ──
  {
    type: 'bar',
    label: '柱状图',
    variants: [
      {
        id: 'bar_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'recommended',
          temporal_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', forbidden: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'bar_unsupported',
      },
      {
        id: 'bar_categorical_aggregated',
        archetypeSuitability: {
          detail_rows: 'allowed_explicit',
          unknown: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', required: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'additiveMeasureFields', maxCount: 1 },
        },
        transform: 'group_by_sum',
        rendererRequirements: [
          {
            capability: 'group_by_sum',
            description: '按 xField 分组对 yField 执行 SUM 聚合',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'bar_needs_aggregation',
      },
    ],
  },

  // ── line ──
  {
    type: 'line',
    label: '折线图',
    variants: [
      {
        id: 'line_temporal_trend_single',
        archetypeSuitability: {
          temporal_series: 'recommended',
        },
        traitRequirements: [
          { trait: 'temporalFieldCount', min: 1 },
          { trait: 'timePointCount', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'entityFieldCount', equals: 0 },
        ],
        semanticMode: 'trend',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'temporalField', index: 0 },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'line_temporal_unsupported',
      },
      {
        id: 'line_temporal_trend_multi',
        archetypeSuitability: {
          multi_series_temporal: 'recommended',
        },
        traitRequirements: [
          { trait: 'temporalFieldCount', min: 1 },
          { trait: 'timePointCount', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'entityFieldCount', equals: 1 },
          { trait: 'entityCount', min: 2 },
          { trait: 'multiSeriesEligible', required: true },
        ],
        semanticMode: 'trend',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'temporalField', index: 0 },
          yFields: { source: 'measureFields', maxCount: 1 },
          seriesField: { source: 'entityField' },
        },
        transform: 'none',
        rendererRequirements: [
          {
            capability: 'multi_series_line',
            description: '支持按 seriesField 分系列绘制多条折线',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'line_multi_series_unsupported',
      },
      {
        id: 'line_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'dimensionCardinality', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'duplicateDimensionKeys', forbidden: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'line_categorical_unsupported',
      },
    ],
  },

  // ── boxplot ──
  {
    type: 'boxplot',
    label: '箱线图',
    variants: [
      {
        id: 'boxplot_grouped_distribution',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
          detail_rows: 'allowed_explicit',
          unknown: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'groupedSamplesEligible', required: true },
          { trait: 'dimensionCardinality', min: 2, max: 20 },
        ],
        semanticMode: 'distribution',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          valueField: { source: 'nonAdditiveMeasureField', index: 0 },
        },
        transform: 'boxplot_summary',
        rendererRequirements: [
          {
            capability: 'boxplot_summary',
            description: '按 xField 分组计算 min/Q1/median/Q3/max 五数概括',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'boxplot_unsupported',
      },
    ],
  },
] as const satisfies readonly ChartCapability[];

// ============================================================
// 完整能力矩阵 ALL_CAPABILITIES_V2（13 种图表）
// ============================================================
//
// 阶段 B-1：离线补齐 13 种图表能力矩阵，仅用于 Golden 测试，不接入运行时。
// 运行时仍默认使用 PILOT_CAPABILITIES_V2（见 chartPlannerV2.ts planChartsV2）。
//
// 与报告《图表架构审查报告 V2》§4.3 的差异（因 profiler 实际命名）：
//   - 报告 trait `categoryFieldCount` → 实际 `dimensionFieldCount`
//   - 报告 trait `hasRepeatedCategories` → 实际 `duplicateDimensionKeys`
//   - 报告 trait `isAggregated` → profiler 无对应 boolean trait（aggregationState 为三态枚举）。
//     阶段 B-2：TraitRequirement 已支持 string 字面量相等，area 现用
//     `{ trait: 'aggregationState', equals: 'aggregated' }` 严格表达"仅聚合后时间序列适合面积图"。
//
// transform gate 策略：
//   - transform='none' / 'group_by_sum'：chartDataTransformV2 已稳定支持，可执行
//   - transform='boxplot_summary'：renderer gate=false → unsupported
//   - transform='matrix_aggregate'：未实现 → renderer gate=false → unsupported
//   - pie/donut 用 transform='none'（ECharts 自动算占比，不依赖 percent_of_total）
//   - gauge/combo 用 transform='none'（buildGaugeChart/buildComboChart 自行处理单值/双轴）

export const ALL_CAPABILITIES_V2 = [
  // ── bar（复用 pilot 两个 variant） ──
  {
    type: 'bar',
    label: '柱状图',
    variants: [
      {
        id: 'bar_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'recommended',
          temporal_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', forbidden: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'bar_unsupported',
      },
      {
        id: 'bar_categorical_aggregated',
        archetypeSuitability: {
          detail_rows: 'allowed_explicit',
          unknown: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', required: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'additiveMeasureFields', maxCount: 1 },
        },
        transform: 'group_by_sum',
        rendererRequirements: [
          {
            capability: 'group_by_sum',
            description: '按 xField 分组对 yField 执行 SUM 聚合',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'bar_needs_aggregation',
      },
    ],
  },

  // ── horizontal_bar（与 bar 对称） ──
  {
    type: 'horizontal_bar',
    label: '横向柱状图',
    variants: [
      {
        id: 'horizontal_bar_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'recommended',
          temporal_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', forbidden: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'horizontal_bar_unsupported',
      },
      {
        id: 'horizontal_bar_categorical_aggregated',
        archetypeSuitability: {
          detail_rows: 'allowed_explicit',
          unknown: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'dimensionCardinality', min: 1 },
          { trait: 'duplicateDimensionKeys', required: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'additiveMeasureFields', maxCount: 1 },
        },
        transform: 'group_by_sum',
        rendererRequirements: [
          {
            capability: 'group_by_sum',
            description: '按 xField 分组对 yField 执行 SUM 聚合',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'horizontal_bar_needs_aggregation',
      },
    ],
  },

  // ── line（复用 pilot 三个 variant） ──
  {
    type: 'line',
    label: '折线图',
    variants: [
      {
        id: 'line_temporal_trend_single',
        archetypeSuitability: {
          temporal_series: 'recommended',
        },
        traitRequirements: [
          { trait: 'temporalFieldCount', min: 1 },
          { trait: 'timePointCount', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'entityFieldCount', equals: 0 },
        ],
        semanticMode: 'trend',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'temporalField', index: 0 },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'line_temporal_unsupported',
      },
      {
        id: 'line_temporal_trend_multi',
        archetypeSuitability: {
          multi_series_temporal: 'recommended',
        },
        traitRequirements: [
          { trait: 'temporalFieldCount', min: 1 },
          { trait: 'timePointCount', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'entityFieldCount', equals: 1 },
          { trait: 'entityCount', min: 2 },
          { trait: 'multiSeriesEligible', required: true },
        ],
        semanticMode: 'trend',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'temporalField', index: 0 },
          yFields: { source: 'measureFields', maxCount: 1 },
          seriesField: { source: 'entityField' },
        },
        transform: 'none',
        rendererRequirements: [
          {
            capability: 'multi_series_line',
            description: '支持按 seriesField 分系列绘制多条折线',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'line_multi_series_unsupported',
      },
      {
        id: 'line_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'dimensionCardinality', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'duplicateDimensionKeys', forbidden: true },
        ],
        semanticMode: 'comparison',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'line_categorical_unsupported',
      },
    ],
  },

  // ── area（单实体时间趋势） ──
  {
    type: 'area',
    label: '面积图',
    variants: [
      {
        id: 'area_temporal_trend_single',
        archetypeSuitability: {
          temporal_series: 'recommended',
        },
        traitRequirements: [
          { trait: 'temporalFieldCount', min: 1 },
          { trait: 'timePointCount', min: 2 },
          { trait: 'measureCount', equals: 1 },
          { trait: 'entityFieldCount', max: 1 },
          { trait: 'aggregationState', equals: 'aggregated' },
        ],
        semanticMode: 'trend',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'temporalField', index: 0 },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'area_unsupported',
      },
    ],
  },

  // ── pie（分类占比，用 none，ECharts 自动算占比） ──
  {
    type: 'pie',
    label: '饼图',
    variants: [
      {
        id: 'pie_part_to_whole',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'partToWholeEligible', required: true },
          { trait: 'dimensionCardinality', min: 2, max: 12 },
        ],
        semanticMode: 'part_to_whole',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'pie_unsupported',
      },
    ],
  },

  // ── donut（与 pie 相同规则） ──
  {
    type: 'donut',
    label: '环形图',
    variants: [
      {
        id: 'donut_part_to_whole',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'partToWholeEligible', required: true },
          { trait: 'dimensionCardinality', min: 2, max: 12 },
        ],
        semanticMode: 'part_to_whole',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 1 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'donut_unsupported',
      },
    ],
  },

  // ── scatter（两数值关系） ──
  // xField=measureFields[0]，yFields=measureFields[1]（measureFieldsAfter 跳过首个 measure）。
  // seriesField=entityField（optional enhancement：无 entityField 时跳过，spec 仍有效）。
  {
    type: 'scatter',
    label: '散点图',
    variants: [
      {
        id: 'scatter_numeric_relationship',
        archetypeSuitability: {
          numeric_relationship: 'recommended',
          temporal_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'numericFieldCount', min: 2 },
          { trait: 'measureCount', min: 2 },
        ],
        semanticMode: 'relationship',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'measureField', index: 0 },
          yFields: { source: 'measureFieldsAfter', afterIndex: 0, maxCount: 1 },
          seriesField: { source: 'entityField' },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'scatter_unsupported',
      },
    ],
  },

  // ── bubble（三数值关系） ──
  // xField=measureFields[0]，yFields=measureFields[1]，sizeField=measureFields[2]，三者互异。
  // seriesField=entityField（optional enhancement：同 scatter）。
  {
    type: 'bubble',
    label: '气泡图',
    variants: [
      {
        id: 'bubble_numeric_relationship',
        archetypeSuitability: {
          numeric_relationship: 'recommended',
        },
        traitRequirements: [
          { trait: 'numericFieldCount', min: 3 },
          { trait: 'measureCount', min: 3 },
        ],
        semanticMode: 'relationship',
        maxSuitability: 'recommended',
        fieldMapping: {
          xField: { source: 'measureField', index: 0 },
          yFields: { source: 'measureFieldsAfter', afterIndex: 0, maxCount: 1 },
          sizeField: { source: 'measureField', index: 2 },
          seriesField: { source: 'entityField' },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'bubble_unsupported',
      },
    ],
  },

  // ── radar（多指标剖面） ──
  {
    type: 'radar',
    label: '雷达图',
    variants: [
      {
        id: 'radar_multi_measure_profile',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', min: 3 },
          { trait: 'dimensionFieldCount', min: 1 },
          { trait: 'dimensionCardinality', max: 8 },
        ],
        semanticMode: 'profile',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 4 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'radar_unsupported',
      },
    ],
  },

  // ── heatmap（二维矩阵，需 matrix_aggregate，gate） ──
  {
    type: 'heatmap',
    label: '热力图',
    variants: [
      {
        id: 'heatmap_categorical_matrix',
        archetypeSuitability: {
          categorical_matrix: 'recommended',
        },
        traitRequirements: [
          { trait: 'matrixEligible', required: true },
          { trait: 'dimensionFieldCount', min: 2 },
        ],
        semanticMode: 'distribution',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'dimensionField', index: 0 },
          yFields: { source: 'dimensionFieldsAfter', afterIndex: 0, maxCount: 1 },
          valueField: { source: 'measureField', index: 0 },
        },
        transform: 'matrix_aggregate',
        rendererRequirements: [
          {
            capability: 'matrix_aggregate',
            description: '将长表按两个分类维度透视成矩阵',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'heatmap_matrix_aggregate_unsupported',
      },
    ],
  },

  // ── boxplot（复用 pilot，boxplot_summary gate） ──
  {
    type: 'boxplot',
    label: '箱线图',
    variants: [
      {
        id: 'boxplot_grouped_distribution',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
          detail_rows: 'allowed_explicit',
          unknown: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'groupedSamplesEligible', required: true },
          { trait: 'dimensionCardinality', min: 2, max: 20 },
        ],
        semanticMode: 'distribution',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          xField: { source: 'primaryDimensionField' },
          valueField: { source: 'nonAdditiveMeasureField', index: 0 },
        },
        transform: 'boxplot_summary',
        rendererRequirements: [
          {
            capability: 'boxplot_summary',
            description: '按 xField 分组计算 min/Q1/median/Q3/max 五数概括',
            currentlySupported: true,
          },
        ],
        unsupportedReasonCode: 'boxplot_unsupported',
      },
    ],
  },

  // ── gauge（单值 KPI，用 none，buildGaugeChart 自行取首行） ──
  {
    type: 'gauge',
    label: '仪表盘',
    variants: [
      {
        id: 'gauge_single_value',
        archetypeSuitability: {
          single_value: 'recommended',
        },
        traitRequirements: [
          { trait: 'measureCount', equals: 1 },
          { trait: 'rowCount', equals: 1 },
        ],
        semanticMode: 'kpi',
        maxSuitability: 'recommended',
        fieldMapping: {
          valueField: { source: 'measureField', index: 0 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'gauge_unsupported',
      },
    ],
  },

  // ── combo（双 Y 轴，用 none） ──
  {
    type: 'combo',
    label: '组合图',
    variants: [
      {
        id: 'combo_dual_axis',
        archetypeSuitability: {
          categorical_series: 'allowed_explicit',
          temporal_series: 'allowed_explicit',
        },
        traitRequirements: [
          { trait: 'measureCount', min: 2 },
          { trait: 'dimensionFieldCount', min: 1 },
        ],
        semanticMode: 'profile',
        maxSuitability: 'allowed_explicit',
        fieldMapping: {
          // primaryDimensionField 在 categorical_series 为分类、在 temporal_series 为时间字段
          xField: { source: 'primaryDimensionField' },
          yFields: { source: 'measureFields', maxCount: 2 },
        },
        transform: 'none',
        rendererRequirements: [],
        unsupportedReasonCode: 'combo_unsupported',
      },
    ],
  },
] as const satisfies readonly ChartCapability[];
