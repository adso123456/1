import { createWidgetDashboardChartItem } from '../widgetDashboardSnapshot';
import type { ChartData } from '../types';

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

const chart: ChartData = {
  id: 'chart-1',
  title: '区县排污口统计',
  columns: ['区县', '数量'],
  rows: [{ 区县: '夷陵区', 数量: 889 }],
  spec: {
    type: 'bar',
    xField: '区县',
    yFields: ['数量'],
  },
  explicitType: true,
  dataVersion: 1,
};

test('仪表板项目包含会话、消息、activeSpec 快照、SQL 和刷新时间', () => {
  const item = createWidgetDashboardChartItem(
    { chart, messageId: 'message-1', sql: 'SELECT 1' },
    'session-1',
    1700000000000,
  );
  assert(item.sourceSessionId === 'session-1', 'sessionId 错误');
  assert(item.sourceMessageId === 'message-1', 'messageId 错误');
  assert(item.sourceSql === 'SELECT 1', 'SQL 错误');
  assert(item.lastRefreshedAt === 1700000000000, '刷新时间错误');
  assert(item.addedAt === 1700000000000, '添加时间错误');
  assert(item.chart.spec.type === 'bar', '图表快照错误');
  assert(item.chart !== chart, '图表没有深拷贝');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
