// chartCapabilityResolverV2.ts — V2 Capability 运行时解析器
//
// 阶段 C：matcher + selector resolver，不包含 Planner 选图逻辑。

import type { TraitRequirement, ScalarFieldSelector, MultiFieldSelector } from './chartCapabilityV2.js';
import type { DatasetProfileV2, DatasetTraitsV2 } from './datasetProfilerV2.js';
import type { ChartSpec } from './types.js';

// ============================================================
// FieldSelectorContext
// ============================================================

/** selector 解析上下文：profiler 数据画像 + 可选的用户偏好 spec */
export interface FieldSelectorContext {
  profile: DatasetProfileV2;
  /** 用户显式指定的图表偏好（来自 ChartSpec 或用户切换操作） */
  preferredSpec?: ChartSpec;
}

// ============================================================
// Trait Matcher
// ============================================================

/**
 * 匹配单个 trait 要求。
 *
 * boolean trait：
 *   - equals: traits[trait] === equals
 *   - required: true → traits[trait] === true
 *   - forbidden: true → traits[trait] === false
 *
 * numeric trait：
 *   - equals: traits[trait] === equals
 *   - min/max: 全部满足才通过（null 表示无边界，undefined 表示未指定该方向）
 */
export function matchTraitRequirement(
  requirement: TraitRequirement,
  traits: DatasetTraitsV2,
): boolean {
  const value: boolean | number = traits[requirement.trait];

  // ── boolean trait ──
  if (typeof value === 'boolean') {
    if ('equals' in requirement) return value === requirement.equals;
    if ('required' in requirement) return value === true;
    if ('forbidden' in requirement) return value === false;
    return false;
  }

  // ── numeric trait ──
  if (typeof value === 'number') {
    if ('equals' in requirement) return value === requirement.equals;

    // 从两种 numeric 变体中提取 min/max（它们都是 number|null|undefined）
    const req = requirement as { min?: number | null; max?: number | null };
    if (req.min != null && value < req.min) return false;
    if (req.max != null && value > req.max) return false;
    return true;
  }

  return false;
}

/**
 * 匹配全部 trait 要求——所有条件同时满足才返回 true。
 * 空数组（无要求）视为通过。
 */
export function matchTraitRequirements(
  requirements: readonly TraitRequirement[],
  traits: DatasetTraitsV2,
): boolean {
  if (requirements.length === 0) return true;
  return requirements.every(r => matchTraitRequirement(r, traits));
}

// ============================================================
// Selector Resolver
// ============================================================

/**
 * 解析标量字段选择器，返回单个字段名。
 *
 * 返回规则：
 * - Profiler selector → 从 context.profile 解析
 * - preferredSpec selector → 从 context.preferredSpec 解析（无 preferredSpec 时返回 null）
 * - 字段不存在 / 索引越界 / 类型不匹配 → null
 * - additive/non-additive 通过 profile.traits.measureKinds 过滤
 * - numericField.exclude 会排除指定字段
 * - 不猜测、不回退到其他 selector
 */
export function resolveScalarSelector(
  selector: ScalarFieldSelector,
  context: FieldSelectorContext,
): string | null {
  const { profile, preferredSpec } = context;

  switch (selector.source) {
    // ── Profiler 独有字段 ──
    case 'entityField':
      return profile.entityField;

    case 'regionField':
      return profile.regionField;

    // ── Profiler traits 字段 ──
    case 'primaryDimensionField':
      return profile.traits.primaryDimensionField;

    case 'dimensionField':
      return profile.traits.dimensionFields[selector.index] ?? null;

    // ── Profiler 列分类字段 ──
    case 'temporalField':
      return profile.temporalFields[selector.index] ?? null;

    case 'measureField':
      return profile.measureFields[selector.index] ?? null;

    case 'additiveMeasureField': {
      const additive = profile.measureFields.filter(
        m => profile.traits.measureKinds[m] === 'additive',
      );
      return additive[selector.index] ?? null;
    }

    case 'nonAdditiveMeasureField': {
      const nonAdditive = profile.measureFields.filter(
        m => profile.traits.measureKinds[m] === 'non_additive',
      );
      return nonAdditive[selector.index] ?? null;
    }

    case 'numericField': {
      const exclude = selector.exclude;
      const filtered = exclude
        ? profile.numericFields.filter(f => !exclude.includes(f))
        : profile.numericFields;
      return filtered[0] ?? null;
    }

    // ── preferredSpec 字段 ──
    case 'preferredXField':
      return preferredSpec?.xField ?? null;

    case 'preferredYField':
      return preferredSpec?.yFields?.[selector.index] ?? null;

    case 'preferredSeriesField':
      return preferredSpec?.seriesField ?? null;

    case 'preferredSizeField':
      return preferredSpec?.sizeField ?? null;

    case 'preferredValueField':
      return preferredSpec?.valueField ?? null;
  }
}

/**
 * 解析多字段选择器，返回字段名数组。
 *
 * 返回规则：
 * - Profiler selector → 从 context.profile 解析
 * - preferredSpec selector → 从 context.preferredSpec 解析（无 preferredSpec 时返回 []）
 * - additive/non-additive 通过 profile.traits.measureKinds 过滤
 * - maxCount 截断结果（undefined 表示不限制）
 * - 不猜测、不回退
 */
export function resolveMultiSelector(
  selector: MultiFieldSelector,
  context: FieldSelectorContext,
): string[] {
  const { profile, preferredSpec } = context;

  switch (selector.source) {
    // ── 无过滤 ──
    case 'measureFields':
      return limitMax(profile.measureFields, selector.maxCount);

    case 'dimensionFields':
      return limitMax(profile.traits.dimensionFields, selector.maxCount);

    case 'temporalFields':
      return limitMax(profile.temporalFields, selector.maxCount);

    // ── 按 measureKind 过滤 ──
    case 'additiveMeasureFields': {
      const additive = profile.measureFields.filter(
        m => profile.traits.measureKinds[m] === 'additive',
      );
      return limitMax(additive, selector.maxCount);
    }

    case 'nonAdditiveMeasureFields': {
      const nonAdditive = profile.measureFields.filter(
        m => profile.traits.measureKinds[m] === 'non_additive',
      );
      return limitMax(nonAdditive, selector.maxCount);
    }

    // ── preferredSpec 字段 ──
    case 'preferredYFields':
      return preferredSpec?.yFields ?? [];
  }
}

// ============================================================
// 内部工具
// ============================================================

function limitMax<T>(arr: readonly T[], maxCount?: number): T[] {
  if (maxCount === undefined) return [...arr];
  return arr.slice(0, maxCount);
}
