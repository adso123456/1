import {
  applyCompactChartLayout,
  COMPACT_CHART_HEIGHT,
  getCompactChartHeight,
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
const baseOption: EChartsOption = {
  tooltip,
  legend: { top: 8 },
  grid: { bottom: 40, left: 50, right: 12 },
  xAxis: {
    type: 'category',
    data: ['长阳土家族自治县', '五峰土家族自治县', '夷陵区'],
    axisLabel: { fontSize: 11 },
  },
  yAxis: { type: 'value' },
  series: [{ type: 'bar', data: [12, 8, 21] }],
};

test('非 compact 模式保持原 option 引用和内容', () => {
  const before = JSON.stringify(baseOption);
  const result = applyCompactChartLayout(baseOption, false);
  assert(result === baseOption, '非 compact 不应创建新 option');
  assert(JSON.stringify(baseOption) === before, '非 compact 修改了原 option');
});

test('compact 分类横轴启用旋转、截断和防重叠', () => {
  const result = applyCompactChartLayout(baseOption, true);
  const axis = result.xAxis as {
    axisLabel?: Record<string, unknown>;
  };
  assert(axis.axisLabel?.interval === 0, '未保留全部类目');
  assert(Number(axis.axisLabel?.rotate) > 0, '未旋转长类目');
  assert(axis.axisLabel?.overflow === 'truncate', '未启用截断');
  assert(axis.axisLabel?.hideOverlap === true, '未启用 hideOverlap');
});

test('compact 分类横轴避免重复留白且不修改原 option', () => {
  const before = JSON.stringify(baseOption);
  const result = applyCompactChartLayout(baseOption, true);
  const grid = result.grid as {
    bottom?: number;
    containLabel?: boolean;
    top?: number;
  };
  assert((grid.bottom ?? 0) === 42, 'compact 底部仍存在重复留白');
  assert((grid.top ?? 0) === 48, 'compact 顶部空间不合理');
  assert(grid.containLabel === true, '未约束标签到图表容器');
  assert(JSON.stringify(baseOption) === before, 'compact 覆盖污染了原 option');
});

test('compact 使用独立画布高度，横向柱图高度受上下限约束', () => {
  assert(COMPACT_CHART_HEIGHT === 292, '纵向 compact 画布高度错误');
  assert(getCompactChartHeight() === 292, '默认 compact 高度错误');
  assert(getCompactChartHeight(5) === 280, '少量横向类目高度错误');
  assert(getCompactChartHeight(14) === 322, '横向类目高度未按数量调整');
  assert(getCompactChartHeight(30) === 330, '横向图缺少合理高度上限');
});

test('compact 隐藏轴名称避免与图表标题重叠', () => {
  const withNames: EChartsOption = {
    ...baseOption,
    xAxis: { type: 'category', name: '区县', data: ['夷陵区'] },
    yAxis: { type: 'value', name: '排污口数量' },
  };
  const result = applyCompactChartLayout(withNames, true);
  const xAxis = result.xAxis as { name?: string };
  const yAxis = result.yAxis as { name?: string };
  assert(xAxis.name === '', 'compact x 轴名称未隐藏');
  assert(yAxis.name === '', 'compact y 轴名称未隐藏');
});

test('横向柱状图长 y 轴标签截断且保留 tooltip', () => {
  const horizontal: EChartsOption = {
    tooltip,
    grid: { left: 40 },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: ['长阳土家族自治县', '五峰土家族自治县'],
    },
    series: [{ type: 'bar', data: [12, 8] }],
  };
  const result = applyCompactChartLayout(horizontal, true);
  const axis = result.yAxis as {
    axisLabel?: Record<string, unknown>;
  };
  const grid = result.grid as { left?: number };
  assert(axis.axisLabel?.overflow === 'truncate', 'y 轴长标签未截断');
  assert(axis.axisLabel?.hideOverlap === true, 'y 轴未防重叠');
  assert((grid.left ?? 0) >= 108, 'y 轴标签空间不足');
  assert(result.tooltip === tooltip, 'tooltip 被覆盖，可能丢失完整类目');
});

test('compact 图例使用滚动布局避免撑破浮窗', () => {
  const result = applyCompactChartLayout(baseOption, true);
  const legend = result.legend as Record<string, unknown>;
  assert(legend.type === 'scroll', '图例未切换为滚动布局');
  assert(legend.width === '86%', '图例宽度未限制');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
