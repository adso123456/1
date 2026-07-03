// chartCapabilityV2.ts — V2 图表能力契约（最小试点：bar / line / boxplot）
//
// 阶段 B：仅新增契约定义，不接入运行时。
// 每个 ChartCapability 包含多个 ChartCapabilityVariant，各自独立定义 archetype 匹配、
// trait 要求、语义模式、字段映射、数据转换和 renderer 门槛。

import type { DatasetArchetype } from './datasetProfilerV2.js';
import type { DatasetTraitsV2 } from './datasetProfilerV2.js';

// ============================================================
// 语义模式
// ============================================================

export type ChartSemanticMode =
  | 'comparison'
  | 'trend'
  | 'part_to_whole'
  | 'relationship'
  | 'distribution'
  | 'kpi';

// ============================================================
// 适用性等级
// ============================================================

/** variant 级别的适用性（不含 unsupported——archetype 不匹配时根本不会进入该 variant） */
export type SupportedSuitability = 'recommended' | 'allowed_explicit';

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
 * 类型安全的 trait 要求：
 * - boolean trait：只能用 required / forbidden
 * - numeric trait：只能用 min / max / equals
 * 错误的组合会在编译期被拒绝。
 */
export type TraitRequirement =
  | {
      trait: BooleanTraitName;
      required?: boolean;
      forbidden?: boolean;
    }
  | {
      trait: NumericTraitName;
      min?: number | null;
      max?: number | null;
      equals?: number;
    };

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
  | { source: 'numericField'; exclude?: string[] };

export type MultiFieldSelector =
  | { source: 'measureFields'; maxCount?: number }
  | { source: 'additiveMeasureFields'; maxCount?: number }
  | { source: 'nonAdditiveMeasureFields'; maxCount?: number }
  | { source: 'dimensionFields'; maxCount?: number }
  | { source: 'temporalFields'; maxCount?: number };

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
  /** 能力标识 */
  capability: string;
  /** 人类可读说明 */
  description: string;
  /** 当前 renderer 是否已支持 */
  currentlySupported: boolean;
}

// ============================================================
// ChartCapabilityVariant & ChartCapability
// ============================================================

export interface ChartCapabilityVariant {
  /** 变体标识（全局唯一，如 'temporal_trend_single'） */
  id: string;
  /** archetype → 基础适用性 */
  archetypeSuitability: ArchetypeSuitability;
  /** trait 要求（全部必须满足） */
  traitRequirements: TraitRequirement[];
  /** 语义模式 */
  semanticMode: ChartSemanticMode;
  /** 此 variant 允许的最高 suitability */
  maxSuitability: SupportedSuitability;
  /** 字段映射 */
  fieldMapping: VariantFieldMapping;
  /** 数据转换计划 */
  transform: DataTransformPlan;
  /** renderer 能力要求（全部必须满足） */
  rendererRequirements: RendererRequirement[];
  /** 当此 variant 不可用时的原因代码 */
  unsupportedReasonCode: string;
}

export interface ChartCapability {
  type: string;
  label: string;
  variants: ChartCapabilityVariant[];
}

// ============================================================
// 试点能力清单
// ============================================================

export const PILOT_CAPABILITIES_V2: ChartCapability[] = [
  // ── bar ──
  {
    type: 'bar',
    label: '柱状图',
    variants: [
      {
        id: 'bar_categorical_comparison',
        archetypeSuitability: {
          categorical_series: 'recommended',
          temporal_series: 'recommended',
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
            currentlySupported: false,
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
          multi_entity_temporal: 'recommended',
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
            currentlySupported: false,
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
            currentlySupported: false,
          },
        ],
        unsupportedReasonCode: 'boxplot_unsupported',
      },
    ],
  },
];
