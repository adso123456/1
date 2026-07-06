// chartPipelineV2.ts — V2 Pilot Pipeline：串联 Planner → DataTransform → Renderer 验证
//
// 纯函数，不接入 React/SSE/仪表板。
// 仅支持 pilot 已实现的图表类型和 transform。

import type { Row } from './datasetProfilerV2.js';
import type { ChartSpec, RenderableChartType, ChartData } from './types.js';
import {
  planChartsV2,
  planChartsWithCapabilitiesV2,
  type ChartPlanV2,
  type ChartPlanningResultV2,
  type SelectionSourceV2,
  type ChartIntentV2,
} from './chartPlannerV2.js';
import { ALL_CAPABILITIES_V2 } from './chartCapabilityV2.js';
import {
  executeDataTransformV2,
  type DataTransformResultV2,
} from './chartDataTransformV2.js';
import { buildChartOption } from './chartRegistry.js';

// ============================================================
// 导出类型
// ============================================================

/** Pipeline 输入 */
export interface PrepareChartInputV2 {
  columns: string[];
  rows: Row[];
  source: SelectionSourceV2;
  intent: ChartIntentV2;
  /** user 显式请求的图表类型 */
  requestedChartType?: RenderableChartType;
  /** model/dashboard 提供的样式偏好 */
  preferredSpec?: ChartSpec;
  /** ChartData 字段 */
  id: string;
  /** ChartData 字段 */
  title: string;
  /** ChartData 字段 */
  dataVersion: number;
}

/** Pipeline 结果 */
export interface PrepareChartResultV2 {
  ok: boolean;
  /** 成功时包含完整 ChartData，失败时为 null */
  chart: ChartData | null;
  /** Planner 完整评估结果（含全部 variant） */
  planning: ChartPlanningResultV2;
  /** 选中的计划（失败时可能为 null） */
  selectedPlan: ChartPlanV2 | null;
  /** Transform 执行结果（失败时可能为 null） */
  transformResult: DataTransformResultV2 | null;
  /** 成功时为 null，失败时包含明确错误码 */
  errorCode: string | null;
}

// ============================================================
// prepareChartV2
// ============================================================

/**
 * 从原始数据到可渲染 ChartData 的完整 V2 流水线。
 *
 * 流程：
 * 1. planChartsV2 → 评估全部 variant，选出默认计划
 * 2. 检查 defaultPlan 及 spec
 * 3. executeDataTransformV2 → 执行数据转换
 * 4. 组装 ChartData（使用转换后的 columns / rows / spec）
 * 5. buildChartOption → 验证 Renderer 可消费
 * 6. 成功则返回完整结果，失败则返回明确 errorCode
 *
 * 不修改输入数据。不在成功时伪装渲染验证。
 */
export function prepareChartV2(
  input: PrepareChartInputV2,
): PrepareChartResultV2 {
  // ── 1. Planner ──
  const planning = planChartsV2({
    columns: input.columns,
    rows: input.rows,
    source: input.source,
    intent: input.intent,
    requestedChartType: input.requestedChartType,
    preferredSpec: input.preferredSpec,
  });

  // ── 2. 检查 defaultPlan ──
  if (!planning.defaultPlan) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan: null,
      transformResult: null,
      errorCode: 'no_default_plan',
    };
  }

  const selectedPlan = planning.defaultPlan;

  // ── 3. 检查 spec ──
  if (!selectedPlan.spec) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult: null,
      errorCode: 'selected_plan_missing_spec',
    };
  }

  // ── 4. Transform ──
  const transformResult = executeDataTransformV2({
    columns: input.columns,
    rows: input.rows,
    spec: selectedPlan.spec,
    transform: selectedPlan.transform,
  });

  if (!transformResult.ok) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult,
      errorCode: `transform_failed_${transformResult.errorCode}`,
    };
  }

  // ── 5. 组装 ChartData（使用转换后的数据）──
  const chart: ChartData = {
    id: input.id,
    title: input.title,
    dataVersion: input.dataVersion,
    columns: transformResult.columns,
    rows: transformResult.rows,
    spec: transformResult.spec,
    // explicitType: true — V2 Planner 已审批该 spec，进入 ChartView 后应保留 V2 选择，
    // 阻止旧 pickDefault() 用 getChartTypeAvailability() 二次推荐覆盖
    explicitType: true,
    v2Meta: {
      semanticMode: selectedPlan.semanticMode,
      transform: selectedPlan.transform,
      archetype: planning.profile.archetype,
      variantId: selectedPlan.variantId,
      fallbackNotice: planning.fallbackNotice,
      noChartReason: planning.noChartReason,
    },
  };

  // ── 6. Renderer 验证 ──
  // explicitType: true — 该 spec 已由 V2 Planner 审批通过，旧 Renderer 不得再做推荐判断
  const option = buildChartOption({ ...chart, explicitType: true });
  if (!option) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult,
      errorCode: 'renderer_rejected_plan',
    };
  }

  // ── 成功 ──
  return {
    ok: true,
    chart,
    planning,
    selectedPlan,
    transformResult,
    errorCode: null,
  };
}

// ============================================================
// prepareChartV2All
// ============================================================

/**
 * 与 prepareChartV2 逻辑相同，但使用 ALL_CAPABILITIES_V2（13 种图表类型）
 * 替代默认的 PILOT_CAPABILITIES_V2（3 种类型）。
 *
 * 阶段 B-5B：仅用于 auto no-chart-type 运行时兜底路径。
 * prepareChartV2 保留 PILOT 行为不变。
 */
export function prepareChartV2All(
  input: PrepareChartInputV2,
): PrepareChartResultV2 {
  // ── 1. Planner（ALL_CAPABILITIES_V2）──
  const planning = planChartsWithCapabilitiesV2(
    {
      columns: input.columns,
      rows: input.rows,
      source: input.source,
      intent: input.intent,
      requestedChartType: input.requestedChartType,
      preferredSpec: input.preferredSpec,
    },
    ALL_CAPABILITIES_V2,
  );

  // ── 2. 检查 defaultPlan ──
  if (!planning.defaultPlan) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan: null,
      transformResult: null,
      errorCode: 'no_default_plan',
    };
  }

  const selectedPlan = planning.defaultPlan;

  // ── 3. 检查 spec ──
  if (!selectedPlan.spec) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult: null,
      errorCode: 'selected_plan_missing_spec',
    };
  }

  // ── 4. Transform ──
  const transformResult = executeDataTransformV2({
    columns: input.columns,
    rows: input.rows,
    spec: selectedPlan.spec,
    transform: selectedPlan.transform,
  });

  if (!transformResult.ok) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult,
      errorCode: `transform_failed_${transformResult.errorCode}`,
    };
  }

  // ── 5. 组装 ChartData ──
  const chart: ChartData = {
    id: input.id,
    title: input.title,
    dataVersion: input.dataVersion,
    columns: transformResult.columns,
    rows: transformResult.rows,
    spec: transformResult.spec,
    explicitType: true,
    v2Meta: {
      semanticMode: selectedPlan.semanticMode,
      transform: selectedPlan.transform,
      archetype: planning.profile.archetype,
      variantId: selectedPlan.variantId,
      fallbackNotice: planning.fallbackNotice,
      noChartReason: planning.noChartReason,
    },
  };

  // ── 6. Renderer 验证 ──
  const option = buildChartOption({ ...chart, explicitType: true });
  if (!option) {
    return {
      ok: false,
      chart: null,
      planning,
      selectedPlan,
      transformResult,
      errorCode: 'renderer_rejected_plan',
    };
  }

  // ── 成功 ──
  return {
    ok: true,
    chart,
    planning,
    selectedPlan,
    transformResult,
    errorCode: null,
  };
}
