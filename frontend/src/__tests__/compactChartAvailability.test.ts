import { getCompactChartTypeAvailability } from '../compactChartAvailability';
import { getChartTypeAvailabilityV2 } from '../chartPipelineV2';
import type { ChartData, RenderableChartType } from '../types';

function assert(condition: unknown, message: string) {
  if (!condition) throw new Error(message);
}

function makeChart(categoryCount: number, type: RenderableChartType = 'bar'): ChartData {
  const columns = ['区域', '数量'];
  const rows = Array.from({ length: categoryCount }, (_, index) => ({
    区域: `区域${index + 1}`,
    数量: index + 1,
  }));
  return {
    id: `fixture-${categoryCount}`,
    title: `${categoryCount}分类`,
    columns,
    rows,
    spec: {
      type,
      xField: '区域',
      yFields: ['数量'],
    },
    explicitType: true,
    dataVersion: 1,
  };
}

function find(
  items: ReturnType<typeof getCompactChartTypeAvailability>,
  type: RenderableChartType,
) {
  const item = items.find(value => value.type === type);
  if (!item) throw new Error(`缺少 ${type} 可用性`);
  return item;
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

test('14 分类通用数据能力仍支持饼图，compact 菜单明确禁用', () => {
  const chart = makeChart(14);
  const dataAvailability = getChartTypeAvailabilityV2(chart);
  const dataPie = dataAvailability.find(item => item.type === 'pie');
  assert(dataPie?.supported, '通用数据能力错误禁用了 14 分类饼图');

  const menu = getCompactChartTypeAvailability(
    chart,
    dataAvailability,
    true,
    375,
  );
  const pie = find(menu, 'pie');
  const donut = find(menu, 'donut');
  assert(pie.dataSupported, '饼图数据能力未保留');
  assert(!pie.compactSupported && !pie.selectable, '14 分类饼图仍可在浮窗选择');
  assert(!donut.selectable, '14 分类环形图仍可在浮窗选择');
  assert(pie.menuReason.includes('浮窗中不适合饼图'), '饼图缺少明确禁用原因');
});

test('14 分类横向柱图在 compact 中仍可真正选择', () => {
  const chart = makeChart(14);
  const menu = getCompactChartTypeAvailability(
    chart,
    getChartTypeAvailabilityV2(chart),
    true,
    375,
  );
  const horizontal = find(menu, 'horizontal_bar');
  assert(horizontal.dataSupported, '横向柱图数据能力异常');
  assert(horizontal.compactSupported, '横向柱图被 compact 错误禁用');
  assert(horizontal.selectable && horizontal.spec !== null, '横向柱图缺少可切换 spec');
});

test('5 分类饼图在 compact 中可选，14 分类饼图在完整工作台仍可选', () => {
  const small = makeChart(5);
  const smallPie = find(
    getCompactChartTypeAvailability(
      small,
      getChartTypeAvailabilityV2(small),
      true,
      375,
    ),
    'pie',
  );
  assert(smallPie.selectable, '5 分类饼图未开放真实菜单切换');

  const many = makeChart(14, 'pie');
  const workspacePie = find(
    getCompactChartTypeAvailability(
      many,
      getChartTypeAvailabilityV2(many),
      false,
      375,
    ),
    'pie',
  );
  assert(workspacePie.selectable, 'compact 限制污染了完整工作台');
});

test('不可用类型保持当前类型和菜单打开，可用类型才关闭菜单', () => {
  const chart = makeChart(14);
  const menu = getCompactChartTypeAvailability(
    chart,
    getChartTypeAvailabilityV2(chart),
    true,
    375,
  );
  const select = (
    current: RenderableChartType,
    target: RenderableChartType,
  ) => {
    const availability = find(menu, target);
    return availability.selectable
      ? { current: target, menuOpen: false, reason: '' }
      : { current, menuOpen: true, reason: availability.menuReason };
  };

  const blocked = select('bar', 'pie');
  assert(blocked.current === 'bar', '不可用饼图改变了 localType');
  assert(blocked.menuOpen, '不可用饼图被当作成功切换并关闭菜单');
  assert(blocked.reason.length > 0, '不可用饼图没有返回原因');

  const switched = select('bar', 'horizontal_bar');
  assert(switched.current === 'horizontal_bar', '横向柱图未切换');
  assert(!switched.menuOpen, '成功切换后菜单未关闭');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
