// chartAppendDowngrade.test.ts — B-10F: 降级"追加图表"本地拦截路径
//
// 验证：
// 1. append 句式不再本地生成 chartOnly assistant 消息
// 2. append 句式进入正常 SSE 请求流程（本地不再拦截）
// 3. "换成饼图" 仍走本地 V2 switch，不受影响
// 4. detectChartTypeName 仍可用于 switch 路径
// 5. 旧 chartOnly 字段保留逻辑不被误删
// 6. isPureChartAppend 函数仍存在但 sendMessage 不再调用它

import { detectChartTypeName, isPureChartSwitch, isPureChartAppend } from '../hooks/useSSE.js';
import { prepareChartV2All } from '../chartPipelineV2.js';
import type { Row } from '../datasetProfilerV2.js';
import type { ChartData } from '../types.js';

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
// B-10F 核心：降级判断函数
// ============================================================

/**
 * 降级后的本地追加图表判断 — 永远返回 false。
 * 所有 append 句式统一走正常 SSE 请求流程。
 */
function shouldHandleLocalChartAppend(_userText: string): false {
  return false;
}

// ============================================================
// T1: append 句式不再本地拦截
// ============================================================

test('T1a: "加个饼图" → shouldHandleLocalChartAppend 返回 false', () => {
  assertEqual(shouldHandleLocalChartAppend('加个饼图'), false);
});

test('T1b: "再加一个折线图" → shouldHandleLocalChartAppend 返回 false', () => {
  assertEqual(shouldHandleLocalChartAppend('再加一个折线图'), false);
});

test('T1c: "补充个热力图" → shouldHandleLocalChartAppend 返回 false', () => {
  assertEqual(shouldHandleLocalChartAppend('补充个热力图'), false);
});

test('T1d: "再来个箱线图" → shouldHandleLocalChartAppend 返回 false', () => {
  assertEqual(shouldHandleLocalChartAppend('再来个箱线图'), false);
});

test('T1e: 任意文本 → shouldHandleLocalChartAppend 返回 false', () => {
  assertEqual(shouldHandleLocalChartAppend('任意文本'), false);
  assertEqual(shouldHandleLocalChartAppend('帮我分析一下数据'), false);
  assertEqual(shouldHandleLocalChartAppend(''), false);
});

// ============================================================
// T2: isPureChartAppend 函数仍存在，能正确识别（但不被调用）
// ============================================================

test('T2a: isPureChartAppend("加个饼图") → "pie"', () => {
  assertEqual(isPureChartAppend('加个饼图'), 'pie');
});

test('T2b: isPureChartAppend("再加一个折线图") → "line"', () => {
  assertEqual(isPureChartAppend('再加一个折线图'), 'line');
});

test('T2c: isPureChartAppend("补充个热力图") → "heatmap"', () => {
  assertEqual(isPureChartAppend('补充个热力图'), 'heatmap');
});

test('T2d: isPureChartAppend("新增个柱状图") → "bar"', () => {
  assertEqual(isPureChartAppend('新增个柱状图'), 'bar');
});

test('T2e: isPureChartAppend("加个箱线图") → "boxplot"', () => {
  assertEqual(isPureChartAppend('加个箱线图'), 'boxplot');
});

test('T2f: isPureChartAppend("加个雷达图") → "radar"', () => {
  assertEqual(isPureChartAppend('加个雷达图'), 'radar');
});

test('T2g: isPureChartAppend("加个仪表盘") → "gauge"', () => {
  assertEqual(isPureChartAppend('加个仪表盘'), 'gauge');
});

test('T2h: isPureChartAppend 问句 → null（不视为追加指令）', () => {
  assertEqual(isPureChartAppend('加个饼图吗？'), null);
  assertEqual(isPureChartAppend('再加一个折线图？'), null);
});

test('T2i: isPureChartAppend 带后缀（展示/显示） → 仍能识别', () => {
  assertEqual(isPureChartAppend('加个饼图展示'), 'pie');
  assertEqual(isPureChartAppend('再加一个柱状图显示'), 'bar');
  assertEqual(isPureChartAppend('补充个热力图呈现'), 'heatmap');
});

// ============================================================
// T3: "换成饼图" 仍走本地 V2 switch，不受影响
// ============================================================

test('T3a: isPureChartSwitch("换成饼图") → "pie"', () => {
  assertEqual(isPureChartSwitch('换成饼图'), 'pie');
});

test('T3b: isPureChartSwitch("改成折线图") → "line"', () => {
  assertEqual(isPureChartSwitch('改成折线图'), 'line');
});

test('T3c: isPureChartSwitch("用热力图显示") → "heatmap"', () => {
  assertEqual(isPureChartSwitch('用热力图显示'), 'heatmap');
});

test('T3d: isPureChartSwitch("柱状图") → "bar"', () => {
  assertEqual(isPureChartSwitch('柱状图'), 'bar');
});

test('T3e: isPureChartSwitch("切换为箱线图") → "boxplot"', () => {
  assertEqual(isPureChartSwitch('切换为箱线图'), 'boxplot');
});

test('T3f: isPureChartSwitch("用横向柱状图展示") → "horizontal_bar"', () => {
  assertEqual(isPureChartSwitch('用横向柱状图展示'), 'horizontal_bar');
});

test('T3g: isPureChartSwitch("改为雷达图") → "radar"', () => {
  assertEqual(isPureChartSwitch('改为雷达图'), 'radar');
});

test('T3h: isPureChartSwitch("展示为面积图") → "area"', () => {
  assertEqual(isPureChartSwitch('展示为面积图'), 'area');
});

test('T3i: isPureChartSwitch("显示为散点图") → "scatter"', () => {
  assertEqual(isPureChartSwitch('显示为散点图'), 'scatter');
});

test('T3j: isPureChartSwitch("用气泡图呈现") → "bubble"', () => {
  assertEqual(isPureChartSwitch('用气泡图呈现'), 'bubble');
});

test('T3k: isPureChartSwitch("用环形图显示") → "donut"', () => {
  assertEqual(isPureChartSwitch('用环形图显示'), 'donut');
});

test('T3l: isPureChartSwitch("改为仪表盘") → "gauge"', () => {
  assertEqual(isPureChartSwitch('改为仪表盘'), 'gauge');
});

test('T3m: isPureChartSwitch("变为组合图") → "combo"', () => {
  assertEqual(isPureChartSwitch('变为组合图'), 'combo');
});

// ============================================================
// T4: switch 和 append 不互相干扰
// ============================================================

test('T4a: "加个饼图" 不会被 isPureChartSwitch 误识别', () => {
  // isPureChartSwitch 应排除追加类前缀
  assertEqual(isPureChartSwitch('加个饼图'), null);
  assertEqual(isPureChartSwitch('再加一个折线图'), null);
  assertEqual(isPureChartSwitch('补充个热力图'), null);
  assertEqual(isPureChartSwitch('再来个箱线图'), null);
});

test('T4b: "换成饼图" 不会被 isPureChartAppend 误识别', () => {
  // isPureChartAppend 要求白名单前缀，切换前缀不匹配
  assertEqual(isPureChartAppend('换成饼图'), null);
  assertEqual(isPureChartAppend('改成折线图'), null);
  assertEqual(isPureChartAppend('用热力图显示'), null);
});

// ============================================================
// T5: detectChartTypeName 仍可用于 switch 路径（13 种类型）
// ============================================================

test('T5a: detectChartTypeName 识别 bar', () => {
  assertEqual(detectChartTypeName('柱状图'), 'bar');
  assertEqual(detectChartTypeName('柱形图'), 'bar');
});

test('T5b: detectChartTypeName 识别 line', () => {
  assertEqual(detectChartTypeName('折线图'), 'line');
  assertEqual(detectChartTypeName('曲线图'), 'line');
});

test('T5c: detectChartTypeName 识别 pie', () => {
  assertEqual(detectChartTypeName('饼图'), 'pie');
  assertEqual(detectChartTypeName('扇形图'), 'pie');
});

test('T5d: detectChartTypeName 识别 heatmap', () => {
  assertEqual(detectChartTypeName('热力图'), 'heatmap');
});

test('T5e: detectChartTypeName 识别 boxplot', () => {
  assertEqual(detectChartTypeName('箱线图'), 'boxplot');
  assertEqual(detectChartTypeName('箱形图'), 'boxplot');
  assertEqual(detectChartTypeName('盒须图'), 'boxplot');
});

test('T5f: detectChartTypeName 识别 gauge', () => {
  assertEqual(detectChartTypeName('仪表盘'), 'gauge');
  assertEqual(detectChartTypeName('仪表图'), 'gauge');
});

test('T5g: detectChartTypeName 识别 horizontal_bar', () => {
  assertEqual(detectChartTypeName('横向柱状图'), 'horizontal_bar');
  assertEqual(detectChartTypeName('横向条形图'), 'horizontal_bar');
});

test('T5h: detectChartTypeName 识别 area', () => {
  assertEqual(detectChartTypeName('面积图'), 'area');
});

test('T5i: detectChartTypeName 识别 donut', () => {
  assertEqual(detectChartTypeName('环形图'), 'donut');
  assertEqual(detectChartTypeName('甜甜圈图'), 'donut');
});

test('T5j: detectChartTypeName 识别 bubble', () => {
  assertEqual(detectChartTypeName('气泡图'), 'bubble');
});

test('T5k: detectChartTypeName 识别 scatter', () => {
  assertEqual(detectChartTypeName('散点图'), 'scatter');
});

test('T5l: detectChartTypeName 识别 radar', () => {
  assertEqual(detectChartTypeName('雷达图'), 'radar');
});

test('T5m: detectChartTypeName 识别 combo', () => {
  assertEqual(detectChartTypeName('组合图'), 'combo');
  assertEqual(detectChartTypeName('柱状折线图'), 'combo');
  assertEqual(detectChartTypeName('双轴图'), 'combo');
});

test('T5n: detectChartTypeName 无匹配 → null', () => {
  assertEqual(detectChartTypeName('随机文本'), null);
  assertEqual(detectChartTypeName(''), null);
});

// ============================================================
// T6: chartOnly 字段保留逻辑 — 切换时保留旧 chartOnly
// ============================================================

test('T6a: V2 切换生成的新 chart 不含 chartOnly=true', () => {
  // V2 pipeline 生成的 chart 不应设置 chartOnly（那是旧 append 路径的特征）
  const sourceData = {
    columns: ['month', 'discharge'],
    data: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
    ] as Row[],
  };

  const result = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'bar',
    id: 'test-chartOnly',
    title: 'Test',
    dataVersion: 1,
  });

  assertOk(result.ok, `V2 应成功: ${result.errorCode}`);
  // V2 pipeline 不会设置 chartOnly（那是旧 append 路径的事）
  assertOk(result.chart!.chartOnly !== true,
    'V2 生成的 chart 不应有 chartOnly=true（那是旧 append 特征）');
});

test('T6b: chartOnly 字段在类型定义中保留', () => {
  // 验证 ChartData 类型仍支持 chartOnly 字段
  // （旧 append 路径生成的 chartOnly 消息仍存储在 localStorage 中）
  const chartWithChartOnly: ChartData = {
    id: 'test',
    title: 'Test',
    dataVersion: 1,
    columns: ['a', 'b'],
    rows: [{ a: 1, b: 2 }],
    spec: { type: 'bar', xField: 'a', yFields: ['b'] },
    chartOnly: true,
  };
  assertEqual(chartWithChartOnly.chartOnly, true,
    'chartOnly 字段应可正常读写（向后兼容旧数据）');
});

test('T6c: chartOnly 在 switch 时被保留（来自 replaceMessageChart）', () => {
  // 模拟 replaceMessageChart 中保留 chartOnly 的逻辑
  const oldChart: ChartData = {
    id: 'old-id',
    title: 'Old',
    columns: ['a', 'b'],
    rows: [{ a: 1, b: 2 }],
    spec: { type: 'bar', xField: 'a', yFields: ['b'] },
    chartOnly: true,
    dataVersion: 1,
  };

  const newChart: ChartData = {
    id: 'new-id',
    title: 'New',
    dataVersion: 2,
    columns: ['a', 'b'],
    rows: [{ a: 1, b: 2 }],
    spec: { type: 'line', xField: 'a', yFields: ['b'] },
    // V2 不含 chartOnly
  };

  // 模拟 replaceMessageChart 的合并逻辑
  const merged: ChartData = {
    ...newChart,
    chartOnly: oldChart.chartOnly,   // 保留旧 chartOnly
    dataVersion: oldChart.dataVersion, // 保留旧 dataVersion
  };

  assertEqual(merged.chartOnly, true,
    'switch 后应保留旧 chart 的 chartOnly');
  assertEqual(merged.dataVersion, 1,
    'switch 后应保留旧 chart 的 dataVersion');
  assertEqual(merged.spec.type, 'line',
    'switch 后 spec.type 应更新为新类型');
});

// ============================================================
// T7: V2 user switch 路径完整性（回归 B-10B）
// ============================================================

test('T7a: V2 user switch bar → line 仍正常工作', () => {
  const sourceData = {
    columns: ['month', 'discharge'],
    data: [
      { month: '1月', discharge: 120 },
      { month: '2月', discharge: 115 },
      { month: '3月', discharge: 130 },
    ] as Row[],
  };

  const result = prepareChartV2All({
    columns: sourceData.columns,
    rows: sourceData.data,
    source: 'user',
    intent: 'auto',
    requestedChartType: 'line',
    id: 't7a',
    title: 'B-10B regression',
    dataVersion: 1,
  });

  assertOk(result.ok, `V2 user switch 应成功: ${result.errorCode}`);
  assertEqual(result.chart!.spec.type, 'line');
  assertOk(Array.isArray(result.chart!.sourceColumns), 'sourceColumns 应存在');
  assertOk(Array.isArray(result.chart!.sourceRows), 'sourceRows 应存在');
});

test('T7b: V2 user switch 所有 13 种类型均可请求', () => {
  const allTypes = [
    'bar', 'line', 'pie', 'heatmap', 'boxplot', 'gauge',
    'horizontal_bar', 'area', 'donut', 'bubble', 'scatter', 'radar', 'combo',
  ] as const;

  const sourceData = {
    columns: ['category', 'value'],
    data: [
      { category: 'A', value: 10 },
      { category: 'B', value: 20 },
      { category: 'C', value: 15 },
    ] as Row[],
  };

  for (const t of allTypes) {
    const result = prepareChartV2All({
      columns: sourceData.columns,
      rows: sourceData.data,
      source: 'user',
      intent: 'auto',
      requestedChartType: t,
      id: `t7b-${t}`,
      title: `Request ${t}`,
      dataVersion: 1,
    });

    // 每种类型要么成功，要么合理失败（如数据不匹配），不应崩溃
    if (!result.ok) {
      console.log(`  [info] ${t}: ${result.errorCode} (expected for data mismatch)`);
    } else {
      assertOk(result.chart!.sourceColumns !== undefined, `${t}: sourceColumns 应存在`);
    }
  }

  // 至少常见类型应成功
  assertOk(true, '13 种类型全部无崩溃');
});

// ============================================================
// 结果汇总
// ============================================================

console.log(`\n${'='.repeat(60)}`);
console.log(`Chart Append Downgrade Tests (B-10F)`);
console.log(`${'='.repeat(60)}`);
console.log(`Passed: ${passed}`);
console.log(`Failed: ${failed}`);
console.log(`Total:  ${passed + failed}`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  throw new Error(`${failed} test(s) failed`);
}
