import {
  applyCompactChartLayout,
  getCompactChartHeight,
  getCompactChartStrategy,
} from '../compactChartLayout';
import type { EChartsOption } from 'echarts';

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

let passed = 0;
let failed = 0;
function test(name: string, callback: () => void) {
  try {
    callback();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`[FAIL] ${name}:`, error);
  }
}

const tooltip = { trigger: 'axis' as const };
const makeCategoryOption = (count: number): EChartsOption => ({
  title: { text: '区县统计' },
  tooltip,
  legend: { bottom: 0 },
  grid: { bottom: 80, left: 50, right: 12 },
  xAxis: {
    type: 'category',
    data: Array.from({ length: count }, (_, index) => `区域${index + 1}`),
  },
  yAxis: { type: 'value' },
  series: [{
    type: 'bar',
    name: '排污口数量',
    data: Array.from({ length: count }, (_, index) => index + 1),
  }],
});

test('非 compact 模式保持原 option 引用、标题和完整数据', () => {
  const option = makeCategoryOption(14);
  const before = JSON.stringify(option);
  const result = applyCompactChartLayout(option, false, {
    width: 360,
    chartType: 'bar',
  });
  assert(result === option, '非 compact 不应创建新 option');
  assert(result.title === option.title, '非 compact 标题被修改');
  assert(JSON.stringify(option) === before, '非 compact 修改了原 option');
});

test('6 个及以下分类不启用 dataZoom', () => {
  const option = makeCategoryOption(6);
  const strategy = getCompactChartStrategy(option, {
    width: 360,
    chartType: 'bar',
  });
  const result = applyCompactChartLayout(option, true, {
    width: 360,
    chartType: 'bar',
  });
  assert(strategy.useCategoryDataZoom === false, '少量分类错误启用 dataZoom');
  assert(result.dataZoom === undefined, '少量分类生成了 dataZoom');
  const axis = result.xAxis as { axisLabel?: Record<string, unknown> };
  assert(axis.axisLabel?.interval === 0, '少量分类未显示全部标签');
});

test('超过 6 个分类启用 inside + slider，默认显示约 6～8 个', () => {
  const option = makeCategoryOption(14);
  const before = JSON.stringify(option);
  const context = { width: 375, chartType: 'bar' as const };
  const strategy = getCompactChartStrategy(option, context);
  const result = applyCompactChartLayout(option, true, context);
  const dataZoom = result.dataZoom as Array<Record<string, unknown>>;
  assert(strategy.useCategoryDataZoom === true, '多分类未启用 dataZoom');
  assert(strategy.visibleCategoryCount === 7, '默认可见类目数不合理');
  assert(dataZoom.length === 2, '未同时提供 inside 和 slider');
  assert(dataZoom[0].type === 'inside', '缺少 inside dataZoom');
  assert(dataZoom[1].type === 'slider', '缺少 slider dataZoom');
  assert(dataZoom[1].endValue === 6, 'slider 默认窗口不是 7 个类目');
  assert(Number(dataZoom[1].height) <= 14, 'slider 不够紧凑');
  const grid = result.grid as Record<string, unknown>;
  assert(grid.left === grid.right, '纵向图未使用对称左右边界');
  assert(grid.containLabel === false, '纵向图仍由 containLabel 二次改变绘图区');
  assert(dataZoom[1].left === grid.left, 'slider 起点未与绘图区对齐');
  assert(dataZoom[1].right === grid.right, 'slider 终点未与绘图区对齐');
  assert(JSON.stringify(option) === before, 'dataZoom 污染了原 option 或原始数据');
  assert(result.tooltip === tooltip, 'tooltip 被覆盖');
});

test('宽度变化后默认可见数量和策略会重新计算', () => {
  const option = makeCategoryOption(14);
  const narrow = getCompactChartStrategy(option, {
    width: 320,
    chartType: 'bar',
  });
  const wider = getCompactChartStrategy(option, {
    width: 500,
    chartType: 'bar',
  });
  assert(narrow.visibleCategoryCount === 6, '窄容器默认窗口错误');
  assert(wider.visibleCategoryCount === 8, '宽容器默认窗口错误');
  assert(narrow.xAxisLabelWidth !== wider.xAxisLabelWidth, '宽度变化未影响标签策略');
});

test('bar、line、area、combo 的多分类均采用横轴 dataZoom', () => {
  const option = makeCategoryOption(14);
  for (const chartType of ['bar', 'line', 'area', 'combo'] as const) {
    const strategy = getCompactChartStrategy(option, {
      width: 375,
      chartType,
    });
    assert(strategy.useCategoryDataZoom, `${chartType} 未启用 dataZoom`);
  }
});

test('横向柱图少量分类按约 30px 行高，多分类不被压缩', () => {
  const makeHorizontal = (count: number, long = false): EChartsOption => ({
    tooltip,
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: Array.from(
        { length: count },
        (_, index) => long ? `${index + 1}号超长行政区域名称` : `区域${index + 1}`,
      ),
    },
    series: [{ type: 'bar', data: Array.from({ length: count }, (_, index) => index) }],
  });
  const small = makeHorizontal(5);
  const many = makeHorizontal(14, true);
  const smallHeight = getCompactChartHeight(small, {
    width: 375,
    chartType: 'horizontal_bar',
  });
  const manyHeight = getCompactChartHeight(many, {
    width: 375,
    chartType: 'horizontal_bar',
  });
  const result = applyCompactChartLayout(many, true, {
    width: 375,
    chartType: 'horizontal_bar',
  });
  const axis = result.yAxis as { axisLabel?: Record<string, unknown> };
  const grid = result.grid as Record<string, unknown>;
  assert(smallHeight >= 230 && smallHeight <= 260, '少量横向柱图高度不合理');
  assert(manyHeight >= 500 && manyHeight <= 520, '14 类横向柱图仍被压缩');
  assert(axis.axisLabel?.interval === 0, '横向柱图未保留全部类目');
  assert(axis.axisLabel?.hideOverlap === false, '动态增高后仍隐藏行标签');
  assert(grid.left === grid.right, '横向柱图外边界未保持对称');
  assert(grid.containLabel === true, '横向柱图未使用单一 containLabel 标签策略');
  assert(Number(grid.left) < 40, '横向柱图仍叠加了大 grid.left');
  assert(
    Number(axis.axisLabel?.width) > Number(grid.left),
    '测试数据未覆盖标签宽度与外边界独立计算',
  );
});

test('横向柱图长名称截断但 Tooltip 保持完整', () => {
  const horizontal: EChartsOption = {
    tooltip,
    grid: { left: 120 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: ['长阳土家族自治县', '五峰土家族自治县'],
    },
    series: [{ type: 'bar', data: [12, 8] }],
  };
  const result = applyCompactChartLayout(horizontal, true, {
    width: 360,
    chartType: 'horizontal_bar',
  });
  const axis = result.yAxis as { axisLabel?: Record<string, unknown> };
  const grid = result.grid as { left?: number };
  assert(axis.axisLabel?.overflow === 'truncate', '长名称未截断');
  assert(Number(grid.left) <= 130, '标签挤占过多绘图区');
  assert(result.tooltip === tooltip, '横向柱图 tooltip 被覆盖');
});

test('6 个及以下饼图可用，显示外部标签并优先隐藏图例', () => {
  const pie: EChartsOption = {
    title: { text: '区域占比' },
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: '70%',
      data: ['夷陵区', '西陵区', '点军区'].map((name, index) => ({
        name,
        value: index + 1,
      })),
    }],
  };
  const context = { width: 360, chartType: 'pie' as const };
  const strategy = getCompactChartStrategy(pie, context);
  const result = applyCompactChartLayout(pie, true, context);
  const legend = result.legend as Record<string, unknown>;
  const series = (result.series as Array<Record<string, unknown>>)[0];
  const label = series.label as Record<string, unknown>;
  assert(strategy.compactAvailable, '少量饼图被错误禁用');
  assert(label.show === true, '少量饼图未显示外部标签');
  assert(legend.show === false, '外部标签存在时仍显示底部图例');
  assert(series.radius === '56%', '饼图半径未按容器宽度调整');
});

test('超过 6 个分类的饼图在 compact 标记为不适合且不修改数据', () => {
  const pieData = Array.from({ length: 14 }, (_, index) => ({
    name: `区域${index + 1}`,
    value: index + 1,
  }));
  const pie: EChartsOption = {
    tooltip: { trigger: 'item' },
    legend: {},
    series: [{ type: 'pie', data: pieData }],
  };
  const strategy = getCompactChartStrategy(pie, {
    width: 375,
    chartType: 'pie',
  });
  const result = applyCompactChartLayout(pie, true, {
    width: 375,
    chartType: 'pie',
  });
  const resultData = (
    (result.series as Array<Record<string, unknown>>)[0].data as unknown[]
  );
  assert(strategy.compactAvailable === false, '多分类饼图仍标记为可用');
  assert(
    strategy.compactUnavailableReason?.includes('浮窗中不适合饼图'),
    '缺少明确替代提示',
  );
  assert(resultData.length === 14, '饼图数据被 TopN、聚合或删除');
});

test('compact 隐藏内部标题；单系列隐藏图例，多系列使用滚动图例', () => {
  const single = makeCategoryOption(5);
  const singleResult = applyCompactChartLayout(single, true, {
    width: 375,
    chartType: 'bar',
  });
  const singleLegend = singleResult.legend as Record<string, unknown>;
  assert(singleResult.title === undefined, 'compact 未隐藏内部标题');
  assert(singleLegend.show === false, '单系列图例未隐藏');

  const multi: EChartsOption = {
    ...makeCategoryOption(5),
    series: [
      { type: 'line', name: '流量', data: [1, 2, 3, 4, 5] },
      { type: 'line', name: '水位', data: [5, 4, 3, 2, 1] },
    ],
  };
  const multiResult = applyCompactChartLayout(multi, true, {
    width: 375,
    chartType: 'line',
  });
  const multiLegend = multiResult.legend as Record<string, unknown>;
  assert(multiLegend.show === true, '多系列图例被隐藏');
  assert(multiLegend.type === 'scroll', '多系列图例未使用 scroll');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
