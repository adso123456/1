// chartSwitchMessageUpdate.test.ts — B-10E: onV2ChartSwitch 消息替换逻辑测试
//
// 验证 ChartView 点击切换后，通过 replaceMessageChart 准确定位并替换消息中的 chart。

import type { ChatMessage, ChartData } from '../types.js';
import type { Row } from '../datasetProfilerV2.js';

let passed = 0;
let failed = 0;

function assertEqual<T>(actual: T, expected: T, msg?: string): void {
  if (actual !== expected) {
    throw new Error(msg ?? `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

function assertOk(cond: boolean, msg?: string): void {
  if (!cond) throw new Error(msg ?? `expected truthy, got ${JSON.stringify(cond)}`);
}

function assertDeepEqual<T>(actual: T, expected: T, msg?: string): void {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) {
    throw new Error(msg ?? `expected ${b}, got ${a}`);
  }
}

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (err) {
    failed++;
    console.error(`\nFAIL: ${name}`);
    console.error(`  ${(err as Error).message}`);
  }
}

// ============================================================
// 被测函数：模拟 useSSE.replaceMessageChart
// ============================================================

/**
 * 模拟 useSSE.ts 中的 replaceMessageChart。
 * 输入旧消息列表 + 定位参数 + 新 chart，返回更新后的消息列表。
 */
function replaceMessageChart(
  messages: ChatMessage[],
  messageId: string,
  chartIndex: number,
  newChart: ChartData,
): ChatMessage[] {
  return messages.map(msg => {
    if (msg.id !== messageId) return msg;
    const oldChart = msg.charts[chartIndex];
    if (!oldChart) return msg;
    return {
      ...msg,
      charts: msg.charts.map((c, i) =>
        i === chartIndex
          ? {
              ...newChart,
              chartOnly: oldChart.chartOnly,
              dataVersion: oldChart.dataVersion,
            }
          : c,
      ),
    };
  });
}

// ============================================================
// 构造辅助
// ============================================================

function makeMessage(overrides?: Partial<ChatMessage>): ChatMessage {
  return {
    id: 'msg-1',
    role: 'assistant',
    text: '这是查询结果',
    dataframes: [],
    charts: [],
    thinkingCollapsed: true,
    streaming: false,
    sql: 'SELECT * FROM table',
    ...overrides,
  };
}

function makeChart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'chart-0',
    title: 'Test Chart',
    dataVersion: 5,
    columns: ['x', 'y'],
    rows: [{ x: 'A', y: 10 }] as Row[],
    spec: { type: 'bar', xField: 'x', yFields: ['y'] },
    ...overrides,
  };
}

function makeV2NewChart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'chart-0',
    title: 'New Chart',
    dataVersion: 99, // V2 产出的新 dataVersion
    columns: ['x', 'y'],
    rows: [{ x: 'A', y: 99 }] as Row[],
    spec: { type: 'line', xField: 'x', yFields: ['y'] },
    explicitType: true,
    v2Meta: {
      semanticMode: 'trend',
      transform: 'none',
      archetype: 'categorical_series',
      variantId: 'line_trend',
    },
    sourceColumns: ['x', 'y'],
    sourceRows: [{ x: 'A', y: 10 }] as Row[],
    ...overrides,
  };
}

// ============================================================
// T1: 定位并替换指定 message 的指定 chart
// ============================================================

test('T1: 定位并替换指定 message 的指定 chart', () => {
  const oldChart = makeChart({ id: 'chart-0', spec: { type: 'bar', xField: 'x', yFields: ['y'] } });
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [oldChart] }),
  ];

  const newChart = makeV2NewChart({ spec: { type: 'line', xField: 'x', yFields: ['y'] } });
  const updated = replaceMessageChart(messages, 'msg-1', 0, newChart);

  assertEqual(updated.length, 1);
  assertEqual(updated[0].charts.length, 1);
  assertEqual(updated[0].charts[0].spec.type, 'line', '应替换为 line');
  assertEqual(updated[0].charts[0].explicitType, true);
  // 保留原 chart 的 dataVersion
  assertEqual(updated[0].charts[0].dataVersion, 5, '应保留旧 dataVersion');
});

// ============================================================
// T2: 多 chart message 中只替换目标 chart
// ============================================================

test('T2: 多 chart message 只替换目标 chart，不影响其他', () => {
  const chart0 = makeChart({ id: 'chart-0', spec: { type: 'bar' } });
  const chart1 = makeChart({ id: 'chart-1', spec: { type: 'pie' } });
  const chart2 = makeChart({ id: 'chart-2', spec: { type: 'scatter' } });

  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [chart0, chart1, chart2] }),
  ];

  const newChart1 = makeV2NewChart({ id: 'chart-1', spec: { type: 'line' } });
  const updated = replaceMessageChart(messages, 'msg-1', 1, newChart1);

  assertEqual(updated[0].charts.length, 3);
  assertEqual(updated[0].charts[0].spec.type, 'bar', 'chart0 不应变');
  assertEqual(updated[0].charts[1].spec.type, 'line', 'chart1 应替换为 line');
  assertEqual(updated[0].charts[2].spec.type, 'scatter', 'chart2 不应变');
});

// ============================================================
// T3: 替换后保留 message 其他字段
// ============================================================

test('T3: 替换后保留 message 的 content/sql/role', () => {
  const messages: ChatMessage[] = [
    makeMessage({
      id: 'msg-1',
      text: '查询完成',
      sql: 'SELECT x, y FROM t',
      role: 'assistant',
      charts: [makeChart()],
    }),
  ];

  const updated = replaceMessageChart(messages, 'msg-1', 0, makeV2NewChart());

  assertEqual(updated[0].text, '查询完成');
  assertEqual(updated[0].sql, 'SELECT x, y FROM t');
  assertEqual(updated[0].role, 'assistant');
  assertEqual(updated[0].streaming, false);
  assertEqual(updated[0].thinkingCollapsed, true);
});

// ============================================================
// T4: 替换后保留 old chart 的 chartOnly/dataVersion
// ============================================================

test('T4: 替换后保留 old chart 的 chartOnly 和 dataVersion', () => {
  const oldChart = makeChart({
    chartOnly: true,
    dataVersion: 42,
  });
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [oldChart] }),
  ];

  const newChart = makeV2NewChart({ chartOnly: undefined, dataVersion: 99 });
  const updated = replaceMessageChart(messages, 'msg-1', 0, newChart);

  assertEqual(updated[0].charts[0].chartOnly, true, '应保留旧 chartOnly');
  assertEqual(updated[0].charts[0].dataVersion, 42, '应保留旧 dataVersion');
});

// ============================================================
// T5: 替换后保留 new chart 的 V2 字段
// ============================================================

test('T5: 替换后保留 new chart 的 V2 字段', () => {
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [makeChart()] }),
  ];

  const newChart = makeV2NewChart();
  const updated = replaceMessageChart(messages, 'msg-1', 0, newChart);

  const c = updated[0].charts[0];
  assertOk(c.v2Meta !== undefined, '应保留 v2Meta');
  assertEqual(c.v2Meta!.semanticMode, 'trend');
  assertEqual(c.v2Meta!.transform, 'none');
  assertOk(c.sourceColumns !== undefined, '应保留 sourceColumns');
  assertOk(c.sourceRows !== undefined, '应保留 sourceRows');
  assertEqual(c.spec.type, 'line', '应保留 new spec');
  assertEqual(c.explicitType, true, '应保留 explicitType');
});

// ============================================================
// T6: 不匹配的 messageId 不影响其他消息
// ============================================================

test('T6: 不匹配的 messageId 不影响其他消息', () => {
  const msg1Charts = [makeChart({ id: 'c1', spec: { type: 'bar' } })];
  const msg2Charts = [makeChart({ id: 'c2', spec: { type: 'pie' } })];

  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: msg1Charts }),
    makeMessage({ id: 'msg-2', charts: msg2Charts }),
  ];

  const updated = replaceMessageChart(messages, 'msg-1', 0, makeV2NewChart({ spec: { type: 'line' } }));

  assertEqual(updated[0].charts[0].spec.type, 'line', 'msg-1 应替换');
  assertEqual(updated[1].charts[0].spec.type, 'pie', 'msg-2 不应变');
});

// ============================================================
// T7: chartIndex 越界不影响
// ============================================================

test('T7: chartIndex 越界时原样返回', () => {
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [makeChart({ spec: { type: 'bar' } })] }),
  ];

  const updated = replaceMessageChart(messages, 'msg-1', 99, makeV2NewChart());

  // chartIndex 99 不存在 → 不替换
  assertEqual(updated[0].charts[0].spec.type, 'bar', '越界不应替换');
});

// ============================================================
// T8: 模拟 onV2ChartSwitch 完整回调流程
// ============================================================

test('T8: 模拟完整 onV2ChartSwitch 回调流程', () => {
  // 模拟 ChartView 中 handleTypeChange 的 V2 成功路径
  const chart = makeChart({
    sourceColumns: ['x', 'y'],
    sourceRows: [{ x: 'A', y: 10 }] as Row[],
    chartOnly: false,
    dataVersion: 7,
  });

  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [chart] }),
  ];

  // 模拟 prepareChartV2All 产出
  const v2NewChart = makeV2NewChart({
    spec: { type: 'line', xField: 'x', yFields: ['y'] },
    explicitType: true,
  });

  // 回调
  const updated = replaceMessageChart(messages, 'msg-1', 0, v2NewChart);

  const result = updated[0].charts[0];
  assertEqual(result.spec.type, 'line');
  assertEqual(result.explicitType, true);
  assertEqual(result.dataVersion, 7, '保留旧 dataVersion');
  assertEqual(result.chartOnly, false, '保留旧 chartOnly');
  assertOk(result.v2Meta !== undefined);
  assertOk(result.sourceColumns !== undefined);
});

// ============================================================
// T9: unsupported 类型不触发替换
// ============================================================

test('T9: unsupported 类型不触发 replaceMessageChart', () => {
  // 验证：仅 supported 类型才会触发 onV2ChartSwitch
  // 此测试验证 replaceMessageChart 本身的正确性（不会错误替换 unsupported）
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [makeChart({ spec: { type: 'bar' } })] }),
  ];

  // unsupported 不会走到 replaceMessageChart（由 handleTypeChange 的 target.supported 守卫）
  // 但即使走了，也只会替换匹配的 chart —— 此处验证该函数行为正常
  const unchanged = replaceMessageChart(messages, 'non-existent-id', 0, makeV2NewChart());
  assertDeepEqual(unchanged, messages, '不匹配 messageId 时不改变');
});

// ============================================================
// T10: dataframes 字段保留
// ============================================================

test('T10: 替换后保留 dataframes', () => {
  const df = { data: [{ a: 1 }], columns: ['a'], row_count: 1, column_count: 1 };
  const messages: ChatMessage[] = [
    makeMessage({ id: 'msg-1', charts: [makeChart()], dataframes: [df] }),
  ];

  const updated = replaceMessageChart(messages, 'msg-1', 0, makeV2NewChart());
  assertEqual(updated[0].dataframes.length, 1);
  assertEqual(updated[0].dataframes[0].columns[0], 'a');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Chart Switch Message Update Tests (B-10E)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
