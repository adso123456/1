import type { EChartsOption } from 'echarts';

type AxisOption = {
  type?: string;
  name?: string;
  axisLabel?: Record<string, unknown>;
};

type GridOption = Record<string, unknown> & {
  bottom?: number | string;
  left?: number | string;
  right?: number | string;
  top?: number | string;
};

function mapAxis(
  axis: EChartsOption['xAxis'] | EChartsOption['yAxis'],
  update: (item: AxisOption) => AxisOption,
) {
  if (Array.isArray(axis)) {
    return axis.map(item => update(item as AxisOption));
  }
  return axis ? update(axis as AxisOption) : axis;
}

function mapGrid(
  grid: EChartsOption['grid'],
  update: (item: GridOption) => GridOption,
) {
  if (Array.isArray(grid)) {
    return grid.map(item => update(item as GridOption));
  }
  return grid ? update(grid as GridOption) : update({});
}

function mapLegend(legend: EChartsOption['legend']) {
  const update = (item: Record<string, unknown>) => ({
    ...item,
    type: 'scroll' as const,
    left: 'center',
    width: '86%',
  });
  if (Array.isArray(legend)) {
    return legend.map(item => update(item as Record<string, unknown>));
  }
  return legend ? update(legend as Record<string, unknown>) : legend;
}

function numericSpace(value: number | string | undefined, minimum: number) {
  return typeof value === 'number' ? Math.max(value, minimum) : minimum;
}

export const COMPACT_CHART_HEIGHT = 292;

export function getCompactChartHeight(categoryCount?: number) {
  if (categoryCount === undefined) return COMPACT_CHART_HEIGHT;
  return Math.min(330, Math.max(280, categoryCount * 18 + 70));
}

/**
 * 仅为浮窗压缩 ECharts 布局。保留原始类目数据和 tooltip，
 * 只覆盖轴标签、图例和 grid，不修改图表 spec 或计算结果。
 */
export function applyCompactChartLayout(
  option: EChartsOption,
  compact: boolean,
): EChartsOption {
  if (!compact) return option;

  let hasCategoryX = false;
  let hasCategoryY = false;

  const xAxis = mapAxis(option.xAxis, item => {
    if (item.type !== 'category') return { ...item, name: '' };
    hasCategoryX = true;
    return {
      ...item,
      name: '',
      axisLabel: {
        ...item.axisLabel,
        interval: 0,
        rotate: 42,
        hideOverlap: true,
        overflow: 'truncate',
        width: 64,
        fontSize: 10,
        margin: 8,
      },
    };
  });

  const yAxis = mapAxis(option.yAxis, item => {
    if (item.type !== 'category') return { ...item, name: '' };
    hasCategoryY = true;
    return {
      ...item,
      name: '',
      axisLabel: {
        ...item.axisLabel,
        hideOverlap: true,
        overflow: 'truncate',
        width: 92,
        fontSize: 10,
      },
    };
  });

  const grid = mapGrid(option.grid, item => ({
    ...item,
    containLabel: true,
    bottom: hasCategoryX
      ? numericSpace(item.bottom, 42)
      : item.bottom,
    left: hasCategoryY
      ? numericSpace(item.left, 108)
      : item.left,
    right: numericSpace(item.right, 18),
    top: numericSpace(item.top, 48),
  }));

  return {
    ...option,
    xAxis,
    yAxis,
    grid,
    legend: mapLegend(option.legend),
  } as EChartsOption;
}
