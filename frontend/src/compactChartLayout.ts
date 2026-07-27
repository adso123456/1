import type { EChartsOption } from 'echarts';
import type { RenderableChartType } from './types';

type AxisOption = {
  type?: string;
  name?: string;
  data?: unknown[];
  axisLabel?: Record<string, unknown>;
};

type GridOption = Record<string, unknown> & {
  bottom?: number | string;
  left?: number | string;
  right?: number | string;
  top?: number | string;
};

type SeriesOption = Record<string, unknown> & {
  type?: string;
  data?: unknown[];
  label?: Record<string, unknown>;
  labelLine?: Record<string, unknown>;
};

export type CompactChartLayoutContext = {
  width?: number;
  chartType?: RenderableChartType;
};

export type CompactChartStrategy = {
  height: number;
  categoryCount: number;
  seriesCount: number;
  maxCategoryNameLength: number;
  visibleCategoryCount: number;
  useCategoryDataZoom: boolean;
  xAxisInterval: number;
  xAxisRotation: number;
  xAxisLabelWidth: number;
  yAxisLabelWidth: number;
  gridLeft: number;
  gridRight: number;
  containLabel: boolean;
  gridBottom: number;
  gridTop: number;
  legendWidth: string;
  showLegend: boolean;
  showPieLabels: boolean;
  pieRadius: string;
  compactAvailable: boolean;
  compactUnavailableReason: string | null;
};

function axisItems(
  axis: EChartsOption['xAxis'] | EChartsOption['yAxis'],
): AxisOption[] {
  if (!axis) return [];
  return (Array.isArray(axis) ? axis : [axis]) as AxisOption[];
}

function seriesItems(series: EChartsOption['series']): SeriesOption[] {
  if (!series) return [];
  return (Array.isArray(series) ? series : [series]) as SeriesOption[];
}

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

function mapLegend(
  legend: EChartsOption['legend'],
  strategy: CompactChartStrategy,
) {
  const update = (item: Record<string, unknown>) => ({
    ...item,
    show: strategy.showLegend,
    type: 'scroll' as const,
    orient: 'horizontal' as const,
    left: 'center',
    bottom: 0,
    width: strategy.legendWidth,
    itemWidth: 10,
    itemHeight: 10,
    textStyle: {
      ...(item.textStyle as Record<string, unknown> | undefined),
      fontSize: 10,
      overflow: 'truncate',
      width: strategy.xAxisLabelWidth,
    },
  });
  if (Array.isArray(legend)) {
    return legend.map(item => update(item as Record<string, unknown>));
  }
  return legend ? update(legend as Record<string, unknown>) : legend;
}

function mapSeries(
  series: EChartsOption['series'],
  strategy: CompactChartStrategy,
) {
  const update = (item: SeriesOption): SeriesOption => {
    if (item.type !== 'pie') return item;
    const radius = Array.isArray(item.radius)
      ? [item.radius[0], strategy.pieRadius]
      : strategy.pieRadius;
    return {
      ...item,
      radius,
      center: ['50%', '46%'],
      label: {
        ...item.label,
        show: strategy.showPieLabels,
        overflow: 'truncate',
        width: strategy.xAxisLabelWidth,
        fontSize: 10,
      },
      labelLine: {
        ...item.labelLine,
        show: strategy.showPieLabels,
        length: 8,
        length2: 6,
      },
    };
  };
  if (Array.isArray(series)) {
    return series.map(item => update(item as SeriesOption));
  }
  return series ? update(series as SeriesOption) : series;
}

function buildDataZoom(
  current: EChartsOption['dataZoom'],
  strategy: CompactChartStrategy,
): EChartsOption['dataZoom'] {
  if (!strategy.useCategoryDataZoom) return current;
  return [
    {
      type: 'inside',
      xAxisIndex: 0,
      startValue: 0,
      endValue: strategy.visibleCategoryCount - 1,
      filterMode: 'none',
      zoomLock: true,
    },
    {
      type: 'slider',
      show: true,
      xAxisIndex: 0,
      startValue: 0,
      endValue: strategy.visibleCategoryCount - 1,
      filterMode: 'none',
      zoomLock: true,
      bottom: 8,
      height: 14,
      left: strategy.gridLeft,
      right: strategy.gridRight,
      showDetail: false,
      brushSelect: false,
      handleSize: '70%',
      backgroundColor: '#f3f4f6',
      fillerColor: 'rgba(37, 99, 235, 0.24)',
      borderColor: '#d1d5db',
      handleStyle: {
        color: '#2563eb',
        borderColor: '#2563eb',
      },
      moveHandleStyle: {
        color: '#2563eb',
      },
    },
  ];
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function optionSummary(option: EChartsOption) {
  const categoryAxes = [
    ...axisItems(option.xAxis),
    ...axisItems(option.yAxis),
  ].filter(axis => axis.type === 'category');
  const categoryValues = categoryAxes.flatMap(axis => axis.data ?? []);
  const series = seriesItems(option.series);
  const pieCategoryValues = series
    .filter(item => item.type === 'pie')
    .flatMap(item => item.data ?? [])
    .map(item => (
      typeof item === 'object' && item !== null && 'name' in item
        ? (item as { name?: unknown }).name
        : item
    ));
  const categories = [...categoryValues, ...pieCategoryValues]
    .map(value => String(value ?? ''));
  return {
    categoryCount: Math.max(
      0,
      ...categoryAxes.map(axis => axis.data?.length ?? 0),
      pieCategoryValues.length,
    ),
    maxCategoryNameLength: Math.max(
      0,
      ...categories.map(value => Array.from(value).length),
    ),
    seriesCount: series.length,
    hasCategoryX: axisItems(option.xAxis).some(axis => axis.type === 'category'),
    hasCategoryY: axisItems(option.yAxis).some(axis => axis.type === 'category'),
    hasPie: series.some(item => item.type === 'pie'),
  };
}

export function getCompactChartStrategy(
  option: EChartsOption,
  context: CompactChartLayoutContext = {},
): CompactChartStrategy {
  const summary = optionSummary(option);
  const width = clamp(context.width ?? 400, 280, 720);
  const isPie = summary.hasPie
    || context.chartType === 'pie'
    || context.chartType === 'donut';
  const isHorizontal = context.chartType === 'horizontal_bar'
    || summary.hasCategoryY;
  const zoomableCategoryChart = summary.hasCategoryX
    && (
      context.chartType === 'bar'
      || context.chartType === 'line'
      || context.chartType === 'area'
      || context.chartType === 'combo'
    );
  const visibleCategoryCount = clamp(Math.floor(width / 52), 6, 8);
  const useCategoryDataZoom = zoomableCategoryChart
    && summary.categoryCount > 6;
  const compactAvailable = !(isPie && summary.categoryCount > 6);
  const maxVisibleLabels = Math.max(4, Math.floor(width / 52));
  const crowded = summary.categoryCount > maxVisibleLabels
    || summary.maxCategoryNameLength > 7;
  const veryCrowded = summary.categoryCount > maxVisibleLabels * 1.5
    || summary.maxCategoryNameLength > 12;
  const xAxisRotation = useCategoryDataZoom
    ? (summary.maxCategoryNameLength > 7 ? 35 : 0)
    : (crowded ? (veryCrowded ? 45 : 35) : 0);
  const xAxisInterval = useCategoryDataZoom
    ? 0
    : summary.categoryCount > maxVisibleLabels
    ? Math.ceil(summary.categoryCount / maxVisibleLabels) - 1
    : 0;
  const xAxisLabelWidth = clamp(
    Math.floor(width / Math.max(5, Math.min(summary.categoryCount || 5, 8))),
    48,
    72,
  );
  const outerInset = width < 340 ? 22 : 26;
  const verticalInset = width < 340 ? 40 : 44;
  const gridLeft = isHorizontal ? outerInset : verticalInset;
  const gridRight = isHorizontal ? outerInset : verticalInset;
  const containLabel = isHorizontal;
  const yAxisLabelWidth = clamp(
    summary.maxCategoryNameLength * 8 + 12,
    72,
    Math.floor(width * 0.3),
  );
  const showPieLabels = isPie
    && compactAvailable
    && summary.categoryCount <= (width < 380 ? 5 : 6)
    && summary.maxCategoryNameLength <= 8;
  const showLegend = isPie
    ? !showPieLabels && compactAvailable
    : summary.seriesCount > 1;
  const gridBottom = useCategoryDataZoom
    ? (showLegend ? 92 : 72)
    : summary.hasCategoryX
    ? (showLegend ? 64 : (xAxisRotation > 0 ? 42 : 28))
    : (showLegend ? 34 : 24);
  const gridTop = showLegend && !isPie ? 56 : 48;

  let height = 292;
  if (isHorizontal) {
    const rowHeight = summary.maxCategoryNameLength > 8
      ? 32
      : 30;
    height = clamp(
      80 + summary.categoryCount * rowHeight + (showLegend ? 24 : 0),
      240,
      520,
    );
  } else if (isPie) {
    height = clamp(
      300 + (showLegend ? 20 : 0) + (summary.categoryCount > 8 ? 20 : 0),
      310,
      360,
    );
  } else if (summary.hasCategoryX) {
    height = clamp(
      284
        + (xAxisRotation > 0 ? 18 : 0)
        + (showLegend ? 26 : 0)
        + (summary.categoryCount > 12 ? 16 : 0),
      284,
      360,
    );
  }

  return {
    height,
    categoryCount: summary.categoryCount,
    seriesCount: summary.seriesCount,
    maxCategoryNameLength: summary.maxCategoryNameLength,
    visibleCategoryCount,
    useCategoryDataZoom,
    xAxisInterval,
    xAxisRotation,
    xAxisLabelWidth,
    yAxisLabelWidth,
    gridLeft,
    gridRight,
    containLabel,
    gridBottom,
    gridTop,
    legendWidth: width < 380 ? '82%' : '88%',
    showLegend,
    showPieLabels,
    pieRadius: width < 360 ? '48%' : (width < 440 ? '56%' : '62%'),
    compactAvailable,
    compactUnavailableReason: compactAvailable
      ? null
      : '分类较多，浮窗中不适合饼图，可在完整工作台查看',
  };
}

export function getCompactChartHeight(
  option: EChartsOption,
  context: CompactChartLayoutContext = {},
) {
  return getCompactChartStrategy(option, context).height;
}

/**
 * 仅为浮窗压缩 ECharts 布局。保留原始类目数据、tooltip 和图表 spec，
 * 根据当前宽度及 option 内容覆盖轴标签、图例、饼图标签和 grid。
 */
export function applyCompactChartLayout(
  option: EChartsOption,
  compact: boolean,
  context: CompactChartLayoutContext = {},
): EChartsOption {
  if (!compact) return option;

  const strategy = getCompactChartStrategy(option, context);

  const xAxis = mapAxis(option.xAxis, item => {
    if (item.type !== 'category') return { ...item, name: '' };
    return {
      ...item,
      name: '',
      axisLabel: {
        ...item.axisLabel,
        interval: strategy.xAxisInterval,
        rotate: strategy.xAxisRotation,
        hideOverlap: true,
        overflow: 'truncate',
        width: strategy.xAxisLabelWidth,
        fontSize: 10,
        margin: 8,
      },
    };
  });

  const yAxis = mapAxis(option.yAxis, item => {
    if (item.type !== 'category') return { ...item, name: '' };
    return {
      ...item,
      name: '',
      axisLabel: {
        ...item.axisLabel,
        interval: 0,
        hideOverlap: false,
        overflow: 'truncate',
        width: strategy.yAxisLabelWidth,
        fontSize: 10,
      },
    };
  });

  const grid = mapGrid(option.grid, item => ({
    ...item,
    containLabel: strategy.containLabel,
    bottom: strategy.gridBottom,
    left: strategy.gridLeft,
    right: strategy.gridRight,
    top: strategy.gridTop,
  }));

  return {
    ...option,
    title: undefined,
    xAxis,
    yAxis,
    grid,
    legend: mapLegend(option.legend, strategy),
    series: mapSeries(option.series, strategy),
    dataZoom: buildDataZoom(option.dataZoom, strategy),
  } as EChartsOption;
}
