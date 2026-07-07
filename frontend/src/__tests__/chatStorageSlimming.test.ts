// chatStorageSlimming.test.ts — P3B: Chat localStorage 瘦身（剥离/恢复 source 数据）
//
// 验证 stripChartSourceDataForStorage 和 hydrateChartSourceDataFromDataframes：
// 1. 保存前剥离 sourceRows/sourceColumns
// 2. 保留 rows/columns/spec/v2Meta/explicitType/dataframes
// 3. 读取后从最后一个有效 dataframe 恢复 source 数据
// 4. 无 dataframe / 无 v2Meta / 已有 source 时的边界行为
// 5. 不 mutate 原始 messages

import {
  stripChartSourceDataForStorage,
  hydrateChartSourceDataFromDataframes,
} from '../hooks/useSSE.js';
import type { ChatMessage, ChartData } from '../types.js';

// ============================================================
// 断言
// ============================================================

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

function assertDeepEqual(actual: unknown, expected: unknown, msg?: string): void {
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
// 工厂函数
// ============================================================

function makeV2Chart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: {
      semanticMode: 'comparison',
      transform: 'group_by_sum',
      archetype: 'categorical_series',
      variantId: 'bar_v1',
    },
    sourceColumns: ['city', 'cnt', 'extra'],
    sourceRows: [
      { city: '北京', cnt: 50, extra: 'x' },
      { city: '北京', cnt: 50, extra: 'y' },
    ],
    ...overrides,
  };
}

function makeOldChart(overrides?: Partial<ChartData>): ChartData {
  return {
    id: 'msg-1-chart-0',
    title: '旧图表',
    dataVersion: 1,
    columns: ['a', 'b'],
    rows: [{ a: 1, b: 2 }],
    spec: { type: 'bar', xField: 'a', yFields: ['b'] },
    ...overrides,
  };
}

function makeMessage(overrides?: Partial<ChatMessage>): ChatMessage {
  return {
    id: 'msg-1',
    role: 'assistant',
    text: '查询结果',
    dataframes: [
      {
        columns: ['city', 'cnt', 'extra'],
        data: [
          { city: '北京', cnt: 50, extra: 'x' },
          { city: '北京', cnt: 50, extra: 'y' },
        ],
        row_count: 2,
        column_count: 3,
      },
    ],
    charts: [],
    thinkingCollapsed: true,
    streaming: false,
    ...overrides,
  };
}

// ============================================================
// 1. 保存前瘦身：stripChartSourceDataForStorage
// ============================================================

test('T1: V2 chart 的 sourceRows/sourceColumns 被移除', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(stripped[0].charts[0].sourceRows === undefined, 'sourceRows 应为 undefined');
  assertOk(stripped[0].charts[0].sourceColumns === undefined, 'sourceColumns 应为 undefined');
});

test('T2: V2 chart 的 rows 保留', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(Array.isArray(stripped[0].charts[0].rows), 'rows 应保留');
  assertEqual(stripped[0].charts[0].rows.length, 1);
  assertDeepEqual(stripped[0].charts[0].rows[0], { city: '北京', cnt: 100 });
});

test('T3: V2 chart 的 columns 保留', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertDeepEqual(stripped[0].charts[0].columns, ['city', 'cnt']);
});

test('T4: V2 chart 的 spec 保留', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertEqual(stripped[0].charts[0].spec.type, 'bar');
  assertEqual(stripped[0].charts[0].spec.xField, 'city');
  assertDeepEqual(stripped[0].charts[0].spec.yFields, ['cnt']);
});

test('T5: V2 chart 的 v2Meta 保留', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(stripped[0].charts[0].v2Meta !== undefined, 'v2Meta 应保留');
  assertEqual(stripped[0].charts[0].v2Meta!.semanticMode, 'comparison');
  assertEqual(stripped[0].charts[0].v2Meta!.transform, 'group_by_sum');
});

test('T6: V2 chart 的 explicitType 保留', () => {
  const chart = makeV2Chart({ explicitType: true });
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertEqual(stripped[0].charts[0].explicitType, true);
});

test('T7: dataframes[].data 保留', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(Array.isArray(stripped[0].dataframes), 'dataframes 应保留');
  assertEqual(stripped[0].dataframes.length, 1);
  assertDeepEqual(stripped[0].dataframes[0].columns, ['city', 'cnt', 'extra']);
  assertEqual(stripped[0].dataframes[0].data.length, 2);
});

test('T8: 原始 messages 对象不被 mutate', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  // 保存快照
  const beforeSourceRows = JSON.parse(JSON.stringify(messages[0].charts[0].sourceRows));

  stripChartSourceDataForStorage(messages);

  // 原始对象不应改变
  assertOk(messages[0].charts[0].sourceRows !== undefined, '原始 sourceRows 不应被删');
  assertDeepEqual(messages[0].charts[0].sourceRows, beforeSourceRows);
  assertOk(messages[0].charts[0].sourceColumns !== undefined, '原始 sourceColumns 不应被删');
});

test('T9: 无 source 数据的 chart 保持不变', () => {
  const chart = makeOldChart(); // 无 v2Meta，无 sourceRows/sourceColumns
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertDeepEqual(stripped[0].charts[0].columns, ['a', 'b']);
  assertDeepEqual(stripped[0].charts[0].rows, [{ a: 1, b: 2 }]);
  assertEqual(stripped[0].charts[0].spec.type, 'bar');
});

test('T10: 混合 charts（V2 + 旧 + 空 source）全部正确处理', () => {
  const v2Chart = makeV2Chart({ id: 'v2-chart' });
  const oldChart = makeOldChart({ id: 'old-chart' });
  const msg = makeMessage({ charts: [v2Chart, oldChart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  // V2 chart → source 被剥离
  assertOk(stripped[0].charts[0].sourceRows === undefined, 'V2 sourceRows 应剥离');
  assertOk(stripped[0].charts[0].v2Meta !== undefined, 'V2 v2Meta 应保留');
  // 旧 chart → 不变
  assertOk(stripped[0].charts[1].sourceRows === undefined, '旧 chart 本无 sourceRows');
  assertDeepEqual(stripped[0].charts[1].rows, [{ a: 1, b: 2 }]);
});

test('T11: messages 数组无 chart 时不抛错', () => {
  const msg = makeMessage({ charts: [] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertEqual(stripped.length, 1);
  assertEqual(stripped[0].charts.length, 0);
});

// ============================================================
// 2. 读取后恢复：hydrateChartSourceDataFromDataframes
// ============================================================

test('T12: V2 chart 缺失 source 时从 dataframe 恢复 sourceRows', () => {
  // 模拟存储后丢失 source 的 V2 chart
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: {
      semanticMode: 'comparison',
      transform: 'group_by_sum',
      archetype: 'categorical_series',
      variantId: 'bar_v1',
    },
    // sourceRows 和 sourceColumns 已被剥离
  };
  const msg = makeMessage({ charts: [slimChart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertOk(hydrated[0].charts[0].sourceRows !== undefined, 'sourceRows 应被恢复');
  assertDeepEqual(hydrated[0].charts[0].sourceRows, msg.dataframes[0].data);
});

test('T13: V2 chart 缺失 source 时从 dataframe 恢复 sourceColumns', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({ charts: [slimChart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertOk(hydrated[0].charts[0].sourceColumns !== undefined, 'sourceColumns 应被恢复');
  assertDeepEqual(hydrated[0].charts[0].sourceColumns, ['city', 'cnt', 'extra']);
});

test('T14: 恢复后 rows 不受影响', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({ charts: [slimChart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertDeepEqual(hydrated[0].charts[0].rows, [{ city: '北京', cnt: 100 }]);
  assertDeepEqual(hydrated[0].charts[0].columns, ['city', 'cnt']);
});

test('T15: 恢复后 spec/v2Meta 不受影响', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({ charts: [slimChart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertEqual(hydrated[0].charts[0].spec.type, 'bar');
  assertEqual(hydrated[0].charts[0].v2Meta!.semanticMode, 'comparison');
});

test('T16: 原始 messages 不被 hydrate 修改', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({ charts: [slimChart] });
  const messages = [msg];

  hydrateChartSourceDataFromDataframes(messages);

  // 原始 chart 应保持 source 为 undefined
  assertOk(messages[0].charts[0].sourceRows === undefined, '原始 sourceRows 不应被修改');
  assertOk(messages[0].charts[0].sourceColumns === undefined, '原始 sourceColumns 不应被修改');
});

// ============================================================
// 3. 无 dataframe 时
// ============================================================

test('T17: 无 dataframe 时不恢复 source，不抛错', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({ charts: [slimChart], dataframes: [] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 应保持 undefined，不抛错
  assertOk(hydrated[0].charts[0].sourceRows === undefined, '无 dataframe 时 sourceRows 保持 undefined');
  assertOk(hydrated[0].charts[0].sourceColumns === undefined, '无 dataframe 时 sourceColumns 保持 undefined');
});

test('T18: dataframe 为空数组时不抛错', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({
    charts: [slimChart],
    dataframes: [{ columns: [], data: [], row_count: 0, column_count: 0 }],
  });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 空 dataframe 不算有效 → 不恢复
  assertOk(hydrated[0].charts[0].sourceRows === undefined);
});

test('T19: 多 dataframe 时用最后一个有效 dataframe 恢复', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = makeMessage({
    charts: [slimChart],
    dataframes: [
      { columns: ['old_col'], data: [{ old_col: 1 }], row_count: 1, column_count: 1 },
      { columns: ['final_col'], data: [{ final_col: 99 }], row_count: 1, column_count: 1 },
    ],
  });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertDeepEqual(hydrated[0].charts[0].sourceColumns, ['final_col'], '应用最后一个有效 dataframe');
  assertDeepEqual(hydrated[0].charts[0].sourceRows, [{ final_col: 99 }]);
});

// ============================================================
// 4. 旧 chart 无 v2Meta
// ============================================================

test('T20: 旧 chart 无 v2Meta 不强制恢复 source', () => {
  const oldChart = makeOldChart(); // 无 v2Meta，无 source 数据
  const msg = makeMessage({ charts: [oldChart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 旧 chart 不应被注入 source 数据
  assertOk(hydrated[0].charts[0].sourceRows === undefined, '旧 chart 不应恢复 sourceRows');
  assertOk(hydrated[0].charts[0].sourceColumns === undefined, '旧 chart 不应恢复 sourceColumns');
  assertOk(hydrated[0].charts[0].v2Meta === undefined, 'v2Meta 应保持 undefined');
});

// ============================================================
// 5. 已有 source 数据时不覆盖
// ============================================================

test('T21: 已有 sourceRows/sourceColumns 时不覆盖', () => {
  const chart = makeV2Chart(); // 已有 sourceRows/sourceColumns
  // dataframe 内容与 source 不同，用来检测是否被覆盖
  const msg = makeMessage({
    charts: [chart],
    dataframes: [
      { columns: ['different'], data: [{ different: 999 }], row_count: 1, column_count: 1 },
    ],
  });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 应保留原始 source 数据，不被 dataframe 覆盖
  assertDeepEqual(hydrated[0].charts[0].sourceColumns, ['city', 'cnt', 'extra']);
  assertDeepEqual(hydrated[0].charts[0].sourceRows, [
    { city: '北京', cnt: 50, extra: 'x' },
    { city: '北京', cnt: 50, extra: 'y' },
  ]);
});

test('T22: 部分有 source（仅 sourceColumns）仍会补全缺失字段', () => {
  const chart: ChartData = {
    ...makeV2Chart(),
    sourceColumns: ['existing_col'], // 有 sourceColumns
    sourceRows: undefined,           // 缺 sourceRows
  };
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // sourceColumns 保留原值，sourceRows 被恢复
  assertDeepEqual(hydrated[0].charts[0].sourceColumns, ['existing_col'], '已有 sourceColumns 不被覆盖');
  assertOk(hydrated[0].charts[0].sourceRows !== undefined, '缺失的 sourceRows 应被恢复');
  assertDeepEqual(hydrated[0].charts[0].sourceRows, msg.dataframes[0].data);
});

// ============================================================
// 6. 端到端：strip → hydrate 往返
// ============================================================

test('T23: strip → hydrate 往返后 chart 数据完整', () => {
  const chart = makeV2Chart();
  const msg = makeMessage({ charts: [chart] });
  const messages = [msg];

  // 1. 模拟保存前剥离
  const stripped = stripChartSourceDataForStorage(messages);
  assertOk(stripped[0].charts[0].sourceRows === undefined, 'strip 后 sourceRows=undefined');
  assertOk(stripped[0].charts[0].sourceColumns === undefined, 'strip 后 sourceColumns=undefined');

  // 2. 模拟读取后恢复
  const hydrated = hydrateChartSourceDataFromDataframes(stripped);
  assertOk(hydrated[0].charts[0].sourceRows !== undefined, 'hydrate 后 sourceRows 恢复');
  assertOk(hydrated[0].charts[0].sourceColumns !== undefined, 'hydrate 后 sourceColumns 恢复');

  // 3. 恢复后的 source 数据应与原始 dataframe 一致
  assertDeepEqual(hydrated[0].charts[0].sourceRows, msg.dataframes[0].data);
  assertDeepEqual(hydrated[0].charts[0].sourceColumns, msg.dataframes[0].columns);

  // 4. 原始 transform 后数据应完整
  assertDeepEqual(hydrated[0].charts[0].rows, [{ city: '北京', cnt: 100 }]);
  assertDeepEqual(hydrated[0].charts[0].columns, ['city', 'cnt']);
});

test('T24: strip → hydrate 往返后旧 chart 不受影响', () => {
  const oldChart = makeOldChart({ id: 'old' });
  const msg = makeMessage({ charts: [oldChart] });
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);
  const hydrated = hydrateChartSourceDataFromDataframes(stripped);

  // 旧 chart 应保持原样
  assertDeepEqual(hydrated[0].charts[0].rows, [{ a: 1, b: 2 }]);
  assertOk(hydrated[0].charts[0].sourceRows === undefined);
  assertOk(hydrated[0].charts[0].v2Meta === undefined);
});

// ============================================================
// 7. 边界：错误 chart
// ============================================================

test('T25: error chart 也被正确剥离和恢复', () => {
  const errorChart: ChartData = {
    id: 'err-chart',
    title: '错误图表',
    dataVersion: 1,
    columns: ['a'],
    rows: [],
    spec: { type: 'none' },
    error: '解析失败',
    v2Meta: { semanticMode: 'comparison', transform: 'none', archetype: 'unknown', variantId: 'err' },
    sourceColumns: ['a'],
    sourceRows: [{ a: 1 }],
  };
  const msg = makeMessage({ charts: [errorChart] });
  const messages = [msg];

  // strip
  const stripped = stripChartSourceDataForStorage(messages);
  assertOk(stripped[0].charts[0].sourceRows === undefined, 'error chart sourceRows 被剥离');
  assertEqual(stripped[0].charts[0].error, '解析失败', 'error 信息保留');

  // hydrate（error chart 有 v2Meta，应恢复）
  const hydrated = hydrateChartSourceDataFromDataframes(stripped);
  assertOk(hydrated[0].charts[0].sourceRows !== undefined, 'error chart sourceRows 被恢复');
  assertEqual(hydrated[0].charts[0].error, '解析失败');
});

// ============================================================
// 8. 旧 schema 兼容：缺失 charts / dataframes（P3C）
// ============================================================

test('T26: strip — 缺失 charts 不抛错，返回 charts: []', () => {
  // 模拟旧版本消息：没有 charts 字段
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', dataframes: [], thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(Array.isArray(stripped[0].charts), 'charts 应为数组');
  assertEqual(stripped[0].charts.length, 0);
});

test('T27: strip — 缺失 dataframes 不抛错，返回 dataframes: []', () => {
  const chart = makeV2Chart();
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', charts: [chart], thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  const stripped = stripChartSourceDataForStorage(messages);

  assertOk(Array.isArray(stripped[0].dataframes), 'dataframes 应为数组');
  assertEqual(stripped[0].dataframes.length, 0);
  // charts 中的 source 应被剥离
  assertOk(stripped[0].charts[0].sourceRows === undefined, 'sourceRows 应被剥离');
});

test('T28: hydrate — 缺失 charts 不抛错，返回 charts: []', () => {
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', dataframes: [], thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertOk(Array.isArray(hydrated[0].charts), 'charts 应为数组');
  assertEqual(hydrated[0].charts.length, 0);
});

test('T29: hydrate — 缺失 dataframes 不抛错，返回 dataframes: []', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', charts: [slimChart], thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  assertOk(Array.isArray(hydrated[0].dataframes), 'dataframes 应为数组');
  assertEqual(hydrated[0].dataframes.length, 0);
  // 无 dataframe 可恢复 → source 保持缺失
  assertOk(hydrated[0].charts[0].sourceRows === undefined);
  assertOk(hydrated[0].charts[0].sourceColumns === undefined);
});

test('T30: 旧消息缺 dataframes 时不影响已有 chart 渲染字段', () => {
  const slimChart: ChartData = {
    id: 'msg-1-chart-0',
    title: '测试图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', charts: [slimChart], thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 渲染必需字段不受影响
  assertDeepEqual(hydrated[0].charts[0].columns, ['city', 'cnt']);
  assertDeepEqual(hydrated[0].charts[0].rows, [{ city: '北京', cnt: 100 }]);
  assertEqual(hydrated[0].charts[0].spec.type, 'bar');
  assertEqual(hydrated[0].charts[0].v2Meta!.semanticMode, 'comparison');
  assertEqual(hydrated[0].charts[0].explicitType, true);
});

test('T31: 缺失 charts 且缺失 dataframes 的消息不抛错', () => {
  // 极旧版本：两个字段都没有
  const msg = { id: 'old-msg', role: 'assistant', text: '旧消息', thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [msg];

  // strip
  const stripped = stripChartSourceDataForStorage(messages);
  assertOk(Array.isArray(stripped[0].charts), 'strip: charts 应为数组');
  assertOk(Array.isArray(stripped[0].dataframes), 'strip: dataframes 应为数组');

  // hydrate
  const hydrated = hydrateChartSourceDataFromDataframes(stripped);
  assertOk(Array.isArray(hydrated[0].charts), 'hydrate: charts 应为数组');
  assertOk(Array.isArray(hydrated[0].dataframes), 'hydrate: dataframes 应为数组');
});

test('T32: 混合 messages — 坏消息不影响正常消息 hydrate', () => {
  // 正常 V2 chart（缺失 source，等待恢复）
  const slimChart: ChartData = {
    id: 'good-chart',
    title: '正常图表',
    dataVersion: 3,
    columns: ['city', 'cnt'],
    rows: [{ city: '北京', cnt: 100 }],
    spec: { type: 'bar', xField: 'city', yFields: ['cnt'] },
    explicitType: true,
    v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_v1' },
  };
  const goodMsg = makeMessage({ id: 'good-msg', charts: [slimChart] });

  // 坏消息：缺少 charts 和 dataframes
  const badMsg = { id: 'bad-msg', role: 'assistant', text: '旧消息', thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;

  const messages = [badMsg, goodMsg] as ChatMessage[];

  const hydrated = hydrateChartSourceDataFromDataframes(messages);

  // 坏消息：normalize 但不抛错
  assertOk(Array.isArray(hydrated[0].charts), '坏消息 charts 应 normalize 为 []');
  assertOk(Array.isArray(hydrated[0].dataframes), '坏消息 dataframes 应 normalize 为 []');

  // 正常消息：source 被成功恢复
  assertOk(hydrated[1].charts[0].sourceRows !== undefined, '正常消息 sourceRows 应被恢复');
  assertDeepEqual(hydrated[1].charts[0].sourceRows, goodMsg.dataframes[0].data);
  assertDeepEqual(hydrated[1].charts[0].sourceColumns, goodMsg.dataframes[0].columns);
  assertDeepEqual(hydrated[1].charts[0].rows, [{ city: '北京', cnt: 100 }]);
});

test('T33: strip — 混合正常 + 旧消息，各自正确 normalize', () => {
  const chart = makeV2Chart();
  const goodMsg = makeMessage({ charts: [chart], id: 'good' });
  const badMsg = { id: 'bad', role: 'assistant', text: '旧消息', thinkingCollapsed: true, streaming: false } as unknown as ChatMessage;
  const messages = [goodMsg, badMsg] as ChatMessage[];

  const stripped = stripChartSourceDataForStorage(messages);

  // 正常消息：source 被剥离
  assertOk(stripped[0].charts[0].sourceRows === undefined, '正常消息 source 被剥离');
  assertOk(Array.isArray(stripped[0].dataframes), '正常消息 dataframes 保留');
  assertEqual(stripped[0].dataframes.length, 1);

  // 旧消息：normalize
  assertOk(Array.isArray(stripped[1].charts), '旧消息 charts normalize');
  assertEqual(stripped[1].charts.length, 0);
  assertOk(Array.isArray(stripped[1].dataframes), '旧消息 dataframes normalize');
  assertEqual(stripped[1].dataframes.length, 0);
});

// ============================================================
// 结果摘要
// ============================================================

console.log(`\n=== chatStorageSlimming.test.ts ===`);
console.log(`通过: ${passed}, 失败: ${failed}, 总计: ${passed + failed}`);
if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
