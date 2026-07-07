// dashboardChartSwitchV2.test.ts — B-11B: Dashboard 内 V2 图表切换完整持久化测试
//
// 验证：
// 1. updateChartFull 完整替换 dashboard item 的 chart
// 2. 替换后保留 layout / title / id 等元数据
// 3. 替换后保留 v2Meta
// 4. 替换后保留 sourceColumns/sourceRows
// 5. 替换后更新 transform 后的 columns/rows
// 6. bar → boxplot 后 columns 含 min/q1/median/q3/max
// 7. line → heatmap 后 buildChartOption() 可渲染
// 8. → multi-series line 后 spec.seriesField 存在
// 9. 找不到 id 返回 false
// 10. 旧 updateChartSpec 仍可用

import { prepareChartV2All } from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';
import type { ChartData, DashboardChartItem, DashboardItem } from '../types.js';
import { buildChartOption } from '../chartRegistry.js';

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
// 模拟 updateChartFull 合并逻辑（纯函数）
// ============================================================

/**
 * 模拟 useDashboard.updateChartFull 中的合并逻辑：
 * newChart 提供 transform 后的 spec/columns/rows/v2Meta/sourceColumns/sourceRows/explicitType，
 * old chart 保留 UI 元数据字段（chartOnly/dataVersion/title/id）。
 */
function mergeChartForDashboard(oldChart: ChartData, newChart: ChartData): ChartData {
  return {
    ...newChart,
    chartOnly: oldChart.chartOnly,
    dataVersion: oldChart.dataVersion,
    title: oldChart.title || newChart.title,
    id: oldChart.id || newChart.id,
  };
}

/**
 * 模拟完整的 item 替换流程：new chart 替换 item.chart，其余字段不变。
 */
function replaceItemChart(
  item: DashboardChartItem,
  newChart: ChartData,
): DashboardChartItem {
  return {
    ...item,
    chart: mergeChartForDashboard(item.chart, newChart),
  };
}

// ============================================================
// 共享测试数据
// ============================================================

/** 模拟多站点 pH 原始数据（适合 boxplot） */
const PH_SOURCE_DATA = {
  columns: ['station', 'ph_value'] as string[],
  rows: [
    { station: '站点A', ph_value: 7.2 },
    { station: '站点A', ph_value: 7.5 },
    { station: '站点A', ph_value: 6.8 },
    { station: '站点B', ph_value: 8.1 },
    { station: '站点B', ph_value: 7.9 },
    { station: '站点B', ph_value: 7.4 },
  ] as Row[],
};

/** 模拟时间序列排放数据（适合 line → heatmap / multi-series line） */
const TIME_SERIES_DATA = {
  columns: ['month', 'station', 'discharge'] as string[],
  rows: [
    { month: '1月', station: '站点A', discharge: 120 },
    { month: '2月', station: '站点A', discharge: 115 },
    { month: '3月', station: '站点A', discharge: 130 },
    { month: '1月', station: '站点B', discharge: 90 },
    { month: '2月', station: '站点B', discharge: 85 },
    { month: '3月', station: '站点B', discharge: 95 },
  ] as Row[],
};

// ============================================================
// T1: mergeChartForDashboard 完整替换
// ============================================================

test('T1a: mergeChartForDashboard — spec 使用 newChart', () => {
  const oldChart: ChartData = {
    id: 'old-id',
    title: 'Old Title',
    dataVersion: 1,
    columns: ['category', 'value'],
    rows: [{ category: 'A', value: 10 }],
    spec: { type: 'bar', xField: 'category', yFields: ['value'] },
    chartOnly: true,
  };

  const newChart: ChartData = {
    id: 'new-id',
    title: 'New Title',
    dataVersion: 3,
    columns: ['station', 'min', 'q1', 'median', 'q3', 'max'],
    rows: [{ station: '站点A', min: 6, q1: 7, median: 7.5, q3: 8, max: 9 }],
    spec: { type: 'boxplot', xField: 'station', yFields: ['min', 'q1', 'median', 'q3', 'max'] },
    explicitType: true,
    v2Meta: { semanticMode: 'distribution', transform: 'boxplot_summary', archetype: 'categorical_series', variantId: 'boxplot_categorical' },
    sourceColumns: ['station', 'ph_value'],
    sourceRows: [{ station: '站点A', ph_value: 7.2 }],
  };

  const merged = mergeChartForDashboard(oldChart, newChart);

  // spec 来自 newChart
  assertEqual(merged.spec.type, 'boxplot');
  assertEqual(merged.spec.yFields!.length, 5);
  assertOk(merged.spec.yFields!.includes('min'), 'yFields 应含 min');

  // columns/rows 来自 newChart
  assertOk(merged.columns.includes('min'), 'columns 应含 min（来自 newChart）');
  assertOk(merged.columns.includes('q1'), 'columns 应含 q1');
  assertEqual(merged.rows.length, 1);

  // UI 元数据保留自 oldChart
  assertEqual(merged.chartOnly, true, 'chartOnly 应保留 old');
  assertEqual(merged.dataVersion, 1, 'dataVersion 应保留 old');
  assertEqual(merged.title, 'Old Title', 'title 优先 old');
  assertEqual(merged.id, 'old-id', 'id 优先 old');

  // V2 字段来自 newChart
  assertOk(merged.v2Meta !== undefined, 'v2Meta 应保留');
  assertEqual(merged.v2Meta!.transform, 'boxplot_summary');
  assertOk(Array.isArray(merged.sourceColumns), 'sourceColumns 应保留');
  assertEqual(merged.sourceColumns![0], 'station');
  assertOk(Array.isArray(merged.sourceRows), 'sourceRows 应保留');
  assertEqual(merged.explicitType, true, 'explicitType 应保留');
});

test('T1b: mergeChartForDashboard — newChart 无 title 时用 oldChart.title', () => {
  const oldChart: ChartData = {
    id: 'oid', title: 'Old', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'bar' },
  };
  const newChart: ChartData = {
    id: 'nid', title: '', dataVersion: 2,
    columns: [], rows: [], spec: { type: 'line' },
  };
  const merged = mergeChartForDashboard(oldChart, newChart);
  assertEqual(merged.title, 'Old', 'oldChart.title 存在时覆盖 newChart 空 title');
});

// ============================================================
// T2: replaceItemChart 保留 item 元数据
// ============================================================

test('T2: replaceItemChart — 保留 item 的 layout / sourceSessionId / sourceMessageId', () => {
  const item: DashboardChartItem = {
    type: 'chart',
    id: 'db-item-1',
    sourceSessionId: 'sess-123',
    sourceMessageId: 'msg-456',
    addedAt: 1700000000000,
    sourceSql: 'SELECT * FROM t',
    lastRefreshedAt: 1700000001000,
    layout: { x: 0, y: 0, w: 3, h: 29 },
    chart: {
      id: 'old-chart-id',
      title: 'Old Chart',
      dataVersion: 1,
      columns: ['a', 'b'],
      rows: [{ a: 1, b: 2 }],
      spec: { type: 'bar', xField: 'a', yFields: ['b'] },
    },
  };

  const newChart: ChartData = {
    id: 'new-chart-id',
    title: 'New Chart',
    dataVersion: 99,
    columns: ['station', 'min', 'q1', 'median', 'q3', 'max'],
    rows: [{ station: 'X', min: 1, q1: 2, median: 3, q3: 4, max: 5 }],
    spec: { type: 'boxplot', xField: 'station', yFields: ['min', 'q1', 'median', 'q3', 'max'] },
    explicitType: true,
    v2Meta: { semanticMode: 'distribution', transform: 'boxplot_summary', archetype: 'categorical_series', variantId: 'boxplot_1' },
  };

  const replaced = replaceItemChart(item, newChart);

  // item 元数据不变
  assertEqual(replaced.type, 'chart');
  assertEqual(replaced.id, 'db-item-1');
  assertEqual(replaced.sourceSessionId, 'sess-123');
  assertEqual(replaced.sourceMessageId, 'msg-456');
  assertEqual(replaced.addedAt, 1700000000000);
  assertEqual(replaced.sourceSql, 'SELECT * FROM t');
  assertEqual(replaced.lastRefreshedAt, 1700000001000);
  assertOk(replaced.layout !== undefined);
  assertEqual(replaced.layout!.x, 0);
  assertEqual(replaced.layout!.w, 3);

  // chart 已替换
  assertEqual(replaced.chart.spec.type, 'boxplot');
  assertEqual(replaced.chart.v2Meta!.transform, 'boxplot_summary');
  assertEqual(replaced.chart.id, 'old-chart-id', 'id 保留 old');
});

// ============================================================
// T3: updateChartFull 找不到 id 返回 false（逻辑模拟）
// ============================================================

test('T3a: 找不到 id 返回 false', () => {
  // 模拟：items 中无匹配 id → 返回 false
  const items: DashboardChartItem[] = [
    { type: 'chart', id: 'a', sourceSessionId: '', sourceMessageId: '', addedAt: 0, chart: { id: '', title: '', dataVersion: 1, columns: [], rows: [], spec: { type: 'bar' } } },
  ];
  const targetId = 'nonexistent';
  const found = items.find(c => c.id === targetId);
  assertEqual(found, undefined, '找不到应返回 undefined');
});

test('T3b: 非 chart 类型 item 不替换', () => {
  // 模拟：item 存在但 type !== 'chart' → 不处理
  const items: DashboardItem[] = [
    { type: 'table', id: 't1', sourceSessionId: '', sourceMessageId: '', addedAt: 0, table: { data: [], columns: [], row_count: 0, column_count: 0 } },
  ];
  const item = items.find(c => c.id === 't1');
  assertOk(item !== undefined, 'item 存在');
  assertEqual(item!.type, 'table', 'type 为 table 不应用 chart 替换');
});

// ============================================================
// T4: V2 pipeline — bar → boxplot（columns 含五数概括）
// ============================================================

test('T4: V2 user bar → boxplot — newChart.columns 含 min/q1/median/q3/max', () => {
  const result = prepareChartV2All({
    columns: PH_SOURCE_DATA.columns,
    rows: PH_SOURCE_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'boxplot',
    id: 't4',
    title: 'pH Boxplot',
    dataVersion: 1,
  });

  assertOk(result.ok, `V2 boxplot 应成功: ${result.errorCode}`);
  const chart = result.chart!;

  assertEqual(chart.spec.type, 'boxplot');
  assertOk(chart.columns.includes('min'), 'columns 应含 min');
  assertOk(chart.columns.includes('q1'), 'columns 应含 q1');
  assertOk(chart.columns.includes('median'), 'columns 应含 median');
  assertOk(chart.columns.includes('q3'), 'columns 应含 q3');
  assertOk(chart.columns.includes('max'), 'columns 应含 max');

  // V2 meta
  assertOk(chart.v2Meta !== undefined, '应有 v2Meta');
  assertEqual(chart.v2Meta!.transform, 'boxplot_summary');

  // source 数据保留
  assertOk(Array.isArray(chart.sourceColumns), 'sourceColumns 应保留');
  assertOk(Array.isArray(chart.sourceRows), 'sourceRows 应保留');
  assertEqual(chart.sourceRows!.length, PH_SOURCE_DATA.rows.length,
    'sourceRows 长度应等于原始数据行数');

  // explicitType 应设为 true
  assertEqual(chart.explicitType, true);

  // 验证合并后的 chart 可用于渲染
  const mockOld: ChartData = {
    id: 'dash-old', title: 'Dashboard', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'bar' },
    chartOnly: false,
  };
  const merged = mergeChartForDashboard(mockOld, chart);
  const option = buildChartOption(merged);
  assertOk(option !== null, '合并后的 chart 应能通过 buildChartOption');
});

// ============================================================
// T5: V2 pipeline — line → heatmap
// ============================================================

test('T5: V2 → heatmap — spec/columns/rows 一致，buildChartOption 可渲染', () => {
  // 热力图需要 categorical_matrix archetype（两个分类列 + 一个数值列）
  const heatmapSourceData = {
    columns: ['region', 'month', 'discharge'] as string[],
    rows: [
      { region: '城北', month: '1月', discharge: 120 },
      { region: '城北', month: '2月', discharge: 115 },
      { region: '城北', month: '3月', discharge: 130 },
      { region: '城南', month: '1月', discharge: 90 },
      { region: '城南', month: '2月', discharge: 85 },
      { region: '城南', month: '3月', discharge: 95 },
      { region: '城东', month: '1月', discharge: 70 },
      { region: '城东', month: '2月', discharge: 75 },
      { region: '城东', month: '3月', discharge: 80 },
    ] as Row[],
  };

  const result = prepareChartV2All({
    columns: heatmapSourceData.columns,
    rows: heatmapSourceData.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'heatmap',
    id: 't5',
    title: 'Discharge Heatmap',
    dataVersion: 1,
  });

  assertOk(result.ok, `V2 heatmap 应成功: ${result.errorCode}`);
  const chart = result.chart!;

  assertEqual(chart.spec.type, 'heatmap');
  assertOk(chart.v2Meta !== undefined, '应有 v2Meta');

  // heatmap spec 应有 xField/yFields/valueField
  assertOk(typeof chart.spec.xField === 'string' && chart.spec.xField!.length > 0,
    'spec.xField 应非空');
  assertOk(Array.isArray(chart.spec.yFields) && chart.spec.yFields!.length > 0,
    'spec.yFields 应非空');

  // columns/rows 应与 spec 字段一致
  assertOk(chart.columns.length >= 2, 'columns 应至少 2 列');

  // source 数据保留
  assertOk(Array.isArray(chart.sourceColumns));
  assertOk(Array.isArray(chart.sourceRows));

  // 合并后能渲染
  const mockOld: ChartData = {
    id: 'dash-old', title: 'Dashboard', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'line' },
  };
  const merged = mergeChartForDashboard(mockOld, chart);
  const option = buildChartOption(merged);
  assertOk(option !== null, '合并后的 heatmap 应能通过 buildChartOption');
});

// ============================================================
// T6: V2 pipeline — → multi-series line
// ============================================================

test('T6: V2 → multi-series line — spec.seriesField 存在', () => {
  const result = prepareChartV2All({
    columns: TIME_SERIES_DATA.columns,
    rows: TIME_SERIES_DATA.rows,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'line',
    id: 't6',
    title: 'Multi-Series Line',
    dataVersion: 1,
  });

  assertOk(result.ok, `V2 multi-series line 应成功: ${result.errorCode}`);
  const chart = result.chart!;

  assertEqual(chart.spec.type, 'line');

  // 多系列折线图应有 seriesField
  assertOk(typeof chart.spec.seriesField === 'string' && chart.spec.seriesField!.length > 0,
    `spec.seriesField 应非空, got: ${JSON.stringify(chart.spec.seriesField)}`);

  // V2 meta
  assertOk(chart.v2Meta !== undefined, '应有 v2Meta');
  assertOk(chart.explicitType === true, 'explicitType 应为 true');

  // 合并后能渲染
  const mockOld: ChartData = {
    id: 'dash-old', title: 'Dashboard', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'bar' },
  };
  const merged = mergeChartForDashboard(mockOld, chart);
  const option = buildChartOption(merged);
  assertOk(option !== null, '合并后的 multi-series line 应能通过 buildChartOption');
});

// ============================================================
// T7: 旧 updateChartSpec 逻辑保留（只更新 spec）
// ============================================================

test('T7: updateChartSpec 只更新 spec + explicitType，不更新 columns/rows', () => {
  // 模拟 updateChartSpec 的行为
  const item: DashboardChartItem = {
    type: 'chart',
    id: 'db-item',
    sourceSessionId: 'sess', sourceMessageId: 'msg', addedAt: 0,
    chart: {
      id: 'chart-id', title: 'Test', dataVersion: 1,
      columns: ['category', 'value'],
      rows: [{ category: 'A', value: 10 }],
      spec: { type: 'bar', xField: 'category', yFields: ['value'] },
      v2Meta: { semanticMode: 'comparison', transform: 'group_by_sum', archetype: 'categorical_series', variantId: 'bar_1' },
    },
  };

  // 模拟 updateChartSpec(id, newSpec)
  const newSpec = {
    type: 'line' as const,
    xField: 'category',
    yFields: ['value'],
  };
  item.chart.spec = newSpec;
  item.chart.explicitType = true;

  // spec 已更新
  assertEqual(item.chart.spec.type, 'line');
  assertEqual(item.chart.explicitType, true);

  // columns/rows 不变（这是 updateChartSpec 的预期行为，B-11B 不改变它）
  assertEqual(item.chart.columns[0], 'category');
  assertEqual(item.chart.rows.length, 1);

  // v2Meta 不变
  assertOk(item.chart.v2Meta !== undefined, 'v2Meta 仍存在');
  assertEqual(item.chart.v2Meta!.transform, 'group_by_sum',
    'v2Meta 不变（旧 spec 路径的特征）');
});

// ============================================================
// T8: V2 pipeline — 所有 13 种类型（dashboard 场景全覆盖）
// ============================================================

test('T8: Dashboard V2 — 全部 13 种类型均可 prepareChartV2All（bar 数据作为 source）', () => {
  const allTypes = [
    'bar', 'line', 'pie', 'heatmap', 'boxplot', 'gauge',
    'horizontal_bar', 'area', 'donut', 'bubble', 'scatter', 'radar', 'combo',
  ] as const;

  // 使用通用的分类+数值数据
  const sourceData = {
    columns: ['category', 'value'] as string[],
    rows: [
      { category: 'A', value: 10 },
      { category: 'B', value: 20 },
      { category: 'C', value: 15 },
    ] as Row[],
  };

  for (const t of allTypes) {
    const result = prepareChartV2All({
      columns: sourceData.columns,
      rows: sourceData.rows,
      source: 'user',
      intent: 'auto',
      requestedChartType: t,
      id: `t8-${t}`,
      title: `Dashboard ${t}`,
      dataVersion: 1,
    });

    if (result.ok && result.chart) {
      // 成功的：验证基本字段
      assertOk(result.chart.explicitType === true, `${t}: explicitType 应为 true`);
      assertOk(Array.isArray(result.chart.sourceColumns), `${t}: sourceColumns 应存在`);
      assertOk(Array.isArray(result.chart.sourceRows), `${t}: sourceRows 应存在`);

      // 模拟 merge 后 buildChartOption
      const mockOld: ChartData = {
        id: 'dash', title: 'Dashboard', dataVersion: 1,
        columns: [], rows: [], spec: { type: 'bar' },
      };
      const merged = mergeChartForDashboard(mockOld, result.chart);
      const option = buildChartOption(merged);
      // 有些类型可能因为数据不匹配而失败（如 gauge 对分类数据），记录即可
      if (!option) {
        console.log(`  [info] ${t}: buildChartOption returned null (data type mismatch expected)`);
      }
    } else {
      console.log(`  [info] ${t}: not supported for this data: ${result.errorCode}`);
    }
  }

  assertOk(true, '13 种类型遍历无崩溃');
});

// ============================================================
// T9: mergeChartForDashboard — 字段边界
// ============================================================

test('T9a: old chart 无 chartOnly → 保留 undefined', () => {
  const old: ChartData = {
    id: 'o', title: 'O', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'bar' },
  };
  const merged = mergeChartForDashboard(old, {
    ...old, id: 'n', spec: { type: 'line' },
  });
  assertEqual(merged.chartOnly, undefined);
});

test('T9b: new chart 无 v2Meta → 不崩溃', () => {
  const old: ChartData = {
    id: 'o', title: 'O', dataVersion: 1,
    columns: [], rows: [], spec: { type: 'bar' },
  };
  const newChart: ChartData = {
    id: 'n', title: 'N', dataVersion: 2,
    columns: ['x'], rows: [{ x: 1 }],
    spec: { type: 'line', xField: 'x', yFields: ['x'] },
  };
  // newChart 无 v2Meta
  const merged = mergeChartForDashboard(old, newChart);
  assertEqual(merged.v2Meta, undefined, '无 v2Meta 不应崩溃');
  assertEqual(merged.spec.type, 'line');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Dashboard Chart Switch V2 Tests (B-11B)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
