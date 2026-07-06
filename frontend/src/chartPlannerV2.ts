// chartPlannerV2.ts — V2 Chart Planner（pilot: bar / line / boxplot）
//
// 阶段 D：遍历 Capability 评估全部 variant，选择默认计划。
// 不包含 DataTransform 执行，不接入运行时。

import { analyzeDatasetV2 } from './datasetProfilerV2.js';
import type { DatasetProfileV2, Row } from './datasetProfilerV2.js';
import { PILOT_CAPABILITIES_V2 } from './chartCapabilityV2.js';
import type {
  ChartCapability,
  ChartCapabilityVariant,
  ChartSemanticMode,
  DataTransformPlan,
  SupportedSuitability,
} from './chartCapabilityV2.js';
import {
  matchTraitRequirements,
  resolveScalarSelector,
  resolveMultiSelector,
} from './chartCapabilityResolverV2.js';
import type { FieldSelectorContext } from './chartCapabilityResolverV2.js';
import type { ChartSpec, ChartSuitability, RenderableChartType } from './types.js';

// ============================================================
// 导出类型
// ============================================================

/** 请求来源 */
export type SelectionSourceV2 = 'auto' | 'model' | 'user' | 'dashboard';

/** 图表意图：auto 或指定语义模式 */
export type ChartIntentV2 = 'auto' | ChartSemanticMode;

/** planChartsV2 输入 */
export interface PlanChartsInputV2 {
  columns: string[];
  rows: Row[];
  source: SelectionSourceV2;
  intent: ChartIntentV2;
  /** user 显式请求的图表类型 */
  requestedChartType?: RenderableChartType;
  /** model/dashboard 提供的样式偏好 */
  preferredSpec?: ChartSpec;
}

/** 单个 variant 的评估结果 */
export interface ChartPlanV2 {
  type: RenderableChartType;
  variantId: string;
  /** archetype 匹配的初始适用性（archetype 不匹配时为 null） */
  baseSuitability: SupportedSuitability | null;
  /** variant 定义的适用性上限 */
  maxSuitability: SupportedSuitability;
  /** 经过 gate + intent 调整后的最终适用性 */
  resolvedSuitability: ChartSuitability;
  semanticMode: ChartSemanticMode;
  /** 解析成功的 ChartSpec（unsupported 时为 null） */
  spec: ChartSpec | null;
  transform: DataTransformPlan;
  /** 不支持原因码（空字符串表示 supported） */
  reasonCode: string;
}

/** planChartsV2 输出 */
export interface ChartPlanningResultV2 {
  profile: DatasetProfileV2;
  /** 全部 variant 的评估结果（含 unsupported） */
  plans: ChartPlanV2[];
  /** 默认选中的计划（无合适计划时为 null） */
  defaultPlan: ChartPlanV2 | null;
  /** 全部计划 unsupported 时的原因说明 */
  noChartReason: string | null;
  /** 请求类型不可用但存在回退计划时的提示 */
  fallbackNotice: string | null;
  /** 用户可切换的备选计划（仅 supported 且 spec !== null） */
  switchablePlans: ChartPlanV2[];
}

// ============================================================
// planChartsV2
// ============================================================

/**
 * 根据输入数据与请求来源，对全部 variant 评估适用性，
 * 并选出默认图表计划。
 *
 * 评估流水线（每个 variant）：
 * 1. archetype 匹配 → 无匹配则 unsupported
 * 2. trait 要求 → 任一不满足则 unsupported
 * 3. renderer 门槛 → currentlySupported===false 则 unsupported
 * 4. 字段选择器解析 → 必需字段缺失则 unsupported（spec=null）
 * 5. suitability 夹紧到 maxSuitability
 * 6. intent 调整（仅 supported）
 *
 * 默认选择：
 * - user：requestedChartType 优先
 * - model/dashboard：preferredSpec.type 优先
 * - auto：第一个 recommended；无 recommended 时 defaultPlan=null
 *
 * 运行时默认使用 PILOT_CAPABILITIES_V2（见 planChartsV2）。
 * Golden 测试使用 ALL_CAPABILITIES_V2（见 planChartsWithCapabilitiesV2）。
 */
export function planChartsWithCapabilitiesV2(
  input: PlanChartsInputV2,
  capabilities: readonly ChartCapability[],
): ChartPlanningResultV2 {
  // ── 数据画像 ──
  const profile = analyzeDatasetV2(input.columns, input.rows);
  const context: FieldSelectorContext = {
    profile,
    preferredSpec: input.preferredSpec,
  };

  // ── 阶段 1：评估全部 variant ──
  const plans: ChartPlanV2[] = [];

  for (const capability of capabilities) {
    for (const variant of capability.variants) {
      const plan = evaluateVariant(
        capability.type,
        variant,
        profile,
        context,
        input.intent,
      );
      plans.push(plan);
    }
  }

  // ── 阶段 2：supported 计划（用于 switchablePlans 和默认选择）──
  const supportedPlans = plans.filter(
    p => p.resolvedSuitability !== 'unsupported' && p.spec !== null,
  );

  // ── 阶段 3：默认选择 ──
  const { defaultPlan, fallbackNotice, noChartReason } = selectDefault(
    plans,
    supportedPlans,
    input,
    profile,
  );

  return {
    profile,
    plans,
    defaultPlan,
    noChartReason,
    fallbackNotice,
    switchablePlans: supportedPlans,
  };
}

/**
 * 运行时入口：使用 PILOT_CAPABILITIES_V2 评估。
 *
 * 阶段 B-1：保持运行时行为不变，不切换到 ALL_CAPABILITIES_V2。
 * Golden 测试请直接调用 planChartsWithCapabilitiesV2(input, ALL_CAPABILITIES_V2)。
 */
export function planChartsV2(input: PlanChartsInputV2): ChartPlanningResultV2 {
  return planChartsWithCapabilitiesV2(input, PILOT_CAPABILITIES_V2);
}

// ============================================================
// Variant 评估
// ============================================================

function evaluateVariant(
  chartType: RenderableChartType,
  variant: ChartCapabilityVariant,
  profile: DatasetProfileV2,
  context: FieldSelectorContext,
  intent: ChartIntentV2,
): ChartPlanV2 {
  // ── 1. archetype 匹配 ──
  const baseSuitability = variant.archetypeSuitability[profile.archetype];
  if (!baseSuitability) {
    return makeUnsupportedPlan(
      chartType, variant, null,
      `archetype_${profile.archetype}_not_in_variant`,
    );
  }

  // ── 2. trait 匹配 ──
  if (!matchTraitRequirements(variant.traitRequirements, profile.traits)) {
    return makeUnsupportedPlan(
      chartType, variant, baseSuitability, variant.unsupportedReasonCode,
    );
  }

  // ── 3. renderer 门槛 ──
  for (const gate of variant.rendererRequirements) {
    if (!gate.currentlySupported) {
      return makeUnsupportedPlan(
        chartType, variant, baseSuitability, variant.unsupportedReasonCode,
      );
    }
  }

  // ── 4. 字段解析与 Spec 构建 ──
  const spec = buildSpec(chartType, variant, context);
  if (!spec) {
    return makeUnsupportedPlan(
      chartType, variant, baseSuitability, variant.unsupportedReasonCode,
    );
  }

  // ── 5. suitability 夹紧 ──
  let resolved = clampSuitability(baseSuitability, variant.maxSuitability);

  // ── 6. intent 调整（仅 supported）──
  resolved = applyIntent(resolved, intent, variant.semanticMode, variant.maxSuitability);

  return {
    type: chartType,
    variantId: variant.id,
    baseSuitability,
    maxSuitability: variant.maxSuitability,
    resolvedSuitability: resolved,
    semanticMode: variant.semanticMode,
    spec,
    transform: variant.transform,
    reasonCode: '',
  };
}

function makeUnsupportedPlan(
  chartType: RenderableChartType,
  variant: ChartCapabilityVariant,
  baseSuitability: SupportedSuitability | null,
  reasonCode: string,
): ChartPlanV2 {
  return {
    type: chartType,
    variantId: variant.id,
    baseSuitability,
    maxSuitability: variant.maxSuitability,
    resolvedSuitability: 'unsupported',
    semanticMode: variant.semanticMode,
    spec: null,
    transform: variant.transform,
    reasonCode,
  };
}

// ============================================================
// Spec 构建
// ============================================================

/**
 * 将 variant 的 fieldMapping 解析为具体 ChartSpec。
 * 任一必需字段解析失败（null 或空数组）→ 返回 null。
 * xField / yFields / valueField / sizeField 为必需；
 * seriesField 为 optional enhancement，解析失败时跳过（不写该字段），不导致 spec=null。
 */
function buildSpec(
  chartType: RenderableChartType,
  variant: ChartCapabilityVariant,
  context: FieldSelectorContext,
): ChartSpec | null {
  const spec: ChartSpec = { type: chartType };
  const fm = variant.fieldMapping;

  // xField — 必需
  if (fm.xField) {
    const x = resolveScalarSelector(fm.xField, context);
    if (!x) return null;
    spec.xField = x;
  }

  // yFields — 存在时必须解析出至少 1 个
  if (fm.yFields) {
    const ys = resolveMultiSelector(fm.yFields, context);
    if (ys.length === 0) return null;
    spec.yFields = ys;
  }

  // valueField — 存在时必须解析成功
  if (fm.valueField) {
    const v = resolveScalarSelector(fm.valueField, context);
    if (!v) return null;
    spec.valueField = v;
  }

  // seriesField — optional enhancement：解析失败时跳过，不让 spec=null
  // （scatter/bubble 的 seriesField 为可选增强，numeric_relationship 数据通常无 entityField）
  if (fm.seriesField) {
    const s = resolveScalarSelector(fm.seriesField, context);
    if (s) spec.seriesField = s;
  }

  // sizeField — 存在时必须解析成功
  if (fm.sizeField) {
    const s = resolveScalarSelector(fm.sizeField, context);
    if (!s) return null;
    spec.sizeField = s;
  }

  return spec;
}

// ============================================================
// Suitability 工具
// ============================================================

/** 将 base suitability 夹紧到 maxSuitability 以下 */
function clampSuitability(
  base: SupportedSuitability,
  maxSuitability: SupportedSuitability,
): ChartSuitability {
  if (base === 'recommended' && maxSuitability === 'allowed_explicit') {
    return 'allowed_explicit';
  }
  return base;
}

/**
 * intent 调整（仅 supported 计划调用）：
 * - auto → 不调整
 * - 匹配 semanticMode → 可提升（不超过 maxSuitability）
 * - 冲突 → recommended 降为 allowed_explicit
 * - unsupported 不调整（不应到达此分支）
 */
function applyIntent(
  current: ChartSuitability,
  intent: ChartIntentV2,
  semanticMode: ChartSemanticMode,
  maxSuitability: SupportedSuitability,
): ChartSuitability {
  if (current === 'unsupported') return 'unsupported';
  if (intent === 'auto') return current;

  if (intent === semanticMode) {
    // 匹配：allowed_explicit → recommended（但不能突破 maxSuitability）
    if (
      current === 'allowed_explicit' &&
      maxSuitability === 'recommended'
    ) {
      return 'recommended';
    }
    return current;
  }

  // 冲突：recommended → allowed_explicit
  if (current === 'recommended') {
    return 'allowed_explicit';
  }
  return current;
}

// ============================================================
// 默认计划选择
// ============================================================

interface DefaultSelection {
  defaultPlan: ChartPlanV2 | null;
  fallbackNotice: string | null;
  noChartReason: string | null;
}

function selectDefault(
  allPlans: readonly ChartPlanV2[],
  supportedPlans: readonly ChartPlanV2[],
  input: PlanChartsInputV2,
  profile: DatasetProfileV2,
): DefaultSelection {
  // 无任何 supported 计划
  if (supportedPlans.length === 0) {
    return {
      defaultPlan: null,
      fallbackNotice: null,
      noChartReason: buildNoChartReason(allPlans),
    };
  }

  const recommended = supportedPlans.filter(p => p.resolvedSuitability === 'recommended');
  const allowedExplicit = supportedPlans.filter(p => p.resolvedSuitability === 'allowed_explicit');

  switch (input.source) {
    case 'user':
      return selectForUser(recommended, allowedExplicit, input.requestedChartType);
    case 'model':
    case 'dashboard':
      return selectForModel(recommended, allowedExplicit, input.preferredSpec?.type);
    case 'auto':
      return selectForAuto(recommended, profile);
  }
}

function selectForUser(
  recommended: readonly ChartPlanV2[],
  allowedExplicit: readonly ChartPlanV2[],
  requestedType?: RenderableChartType,
): DefaultSelection {
  if (requestedType) {
    // 优先匹配 requestedType 的 recommended
    const rec = recommended.find(p => p.type === requestedType);
    if (rec) return { defaultPlan: rec, fallbackNotice: null, noChartReason: null };

    // 其次匹配 requestedType 的 allowed_explicit
    const ae = allowedExplicit.find(p => p.type === requestedType);
    if (ae) return { defaultPlan: ae, fallbackNotice: null, noChartReason: null };

    // requestedType 不可用 → 回退
    const fallback = recommended[0] ?? allowedExplicit[0];
    if (fallback) {
      return {
        defaultPlan: fallback,
        fallbackNotice: `请求的图表类型 "${requestedType}" 不可用，已回退到 "${fallback.variantId}"`,
        noChartReason: null,
      };
    }
    // 不应到达（supportedPlans 非空）
    return { defaultPlan: null, fallbackNotice: null, noChartReason: 'no_fallback_available' };
  }

  // 无请求类型 → 第一个 recommended，否则第一个 allowed_explicit
  const def = recommended[0] ?? allowedExplicit[0] ?? null;
  return { defaultPlan: def, fallbackNotice: null, noChartReason: null };
}

function selectForModel(
  recommended: readonly ChartPlanV2[],
  allowedExplicit: readonly ChartPlanV2[],
  preferredType?: string,
): DefaultSelection {
  if (preferredType) {
    const rec = recommended.find(p => p.type === preferredType);
    if (rec) return { defaultPlan: rec, fallbackNotice: null, noChartReason: null };

    const ae = allowedExplicit.find(p => p.type === preferredType);
    if (ae) return { defaultPlan: ae, fallbackNotice: null, noChartReason: null };

    // preferredType 不可用 → 回退
    const fallback = recommended[0] ?? allowedExplicit[0];
    if (fallback) {
      return {
        defaultPlan: fallback,
        fallbackNotice: `首选图表类型 "${preferredType}" 不可用，已回退到 "${fallback.variantId}"`,
        noChartReason: null,
      };
    }
    return { defaultPlan: null, fallbackNotice: null, noChartReason: 'no_fallback_available' };
  }

  const def = recommended[0] ?? allowedExplicit[0] ?? null;
  return { defaultPlan: def, fallbackNotice: null, noChartReason: null };
}

function selectForAuto(
  recommended: readonly ChartPlanV2[],
  profile: DatasetProfileV2,
): DefaultSelection {
  if (recommended.length > 0) {
    // 稳定 tie-break：按数据形态显式排序，不依赖 capabilities 数组顺序。
    // 复制后排序，避免污染外部数组。
    const sorted = [...recommended].sort(
      (a, b) => autoPriority(a, profile) - autoPriority(b, profile),
    );
    return { defaultPlan: sorted[0], fallbackNotice: null, noChartReason: null };
  }
  return {
    defaultPlan: null,
    fallbackNotice: null,
    noChartReason: 'no_recommended_plan_for_auto',
  };
}

/**
 * auto 默认选择的稳定优先级：返回数字越小越优先。
 *
 * 显式表达"数据形态 → 首选图表类型"，不依赖 ALL_CAPABILITIES_V2 数组顺序：
 *   - single_value → gauge
 *   - categorical_series + 单指标 → bar
 *   - temporal_series → line
 *   - numeric_relationship + measureCount===2 → scatter
 *   - numeric_relationship + measureCount>=3 → bubble
 *
 * 未匹配的数据形态返回大值（不特殊提升），保持 recommended 出现顺序。
 * 仅影响 source==='auto'，user/model/dashboard 不调用此函数。
 */
function autoPriority(plan: ChartPlanV2, profile: DatasetProfileV2): number {
  const arch = profile.archetype;
  const measureCount = profile.traits.measureCount;

  // (archetype, chartType) → 优先级（0 最优先）。未列出的组合走兜底。
  if (arch === 'single_value' && plan.type === 'gauge') return 0;
  if (arch === 'categorical_series' && measureCount === 1 && plan.type === 'bar') return 0;
  if (arch === 'temporal_series' && plan.type === 'line') return 0;
  if (arch === 'numeric_relationship' && measureCount === 2 && plan.type === 'scatter') return 0;
  if (arch === 'numeric_relationship' && measureCount >= 3 && plan.type === 'bubble') return 0;

  // 兜底：同类 archetype 内的次选（如 horizontal_bar vs bar、area vs line）
  // 给一个中等优先级，仍优于完全未匹配的组合。
  if (arch === 'categorical_series' && plan.type === 'horizontal_bar') return 1;
  if (arch === 'temporal_series' && plan.type === 'area') return 1;
  if (arch === 'numeric_relationship' && measureCount >= 3 && plan.type === 'scatter') return 2;

  // 完全未匹配：返回大值，保持相对顺序（sort 稳定，同优先级保持原顺序）
  return 100;
}

function buildNoChartReason(allPlans: readonly ChartPlanV2[]): string {
  const reasons = allPlans
    .filter(p => p.resolvedSuitability === 'unsupported')
    .map(p => `${p.variantId}: ${p.reasonCode}`);
  return reasons.length > 0
    ? `所有图表计划均不可用。原因: ${reasons.join('; ')}`
    : '无可用的图表计划';
}
