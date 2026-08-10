import { readFileSync } from 'node:fs';

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const exportSource = readFileSync(new URL('../utils/dashboardExport.ts', import.meta.url), 'utf8');
const dashboardSource = readFileSync(new URL('../components/DashboardView.tsx', import.meta.url), 'utf8');
const chartSource = readFileSync(new URL('../components/ChartView.tsx', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../App.tsx', import.meta.url), 'utf8');

assert(exportSource.includes('export async function renderDashboardAsPng'), '缺少 PNG 预览生成函数');
assert(!exportSource.includes("root.querySelector('[data-export-header]')"), '导出仍包含仪表板总标题栏');
assert(exportSource.includes('piece.y - minY + padding'), '导出图片没有按卡片边界裁剪');
assert(dashboardSource.includes('仪表板导出预览'), '缺少仪表板导出预览弹窗');
assert(dashboardSource.includes('downloadDashboardPng(exportPreview.blob'), '预览弹窗缺少下载操作');

assert(chartSource.includes("effectiveViewMode === 'table'"), '表格模式缺少导出分支');
assert(chartSource.includes("type: 'text/csv;charset=utf-8'"), '表格模式没有导出 CSV');
assert(chartSource.includes('effectiveViewMode,'), '添加到仪表板没有传递当前图表/表格模式');
assert(appSource.includes("viewMode === 'table'"), '应用层没有生成表格仪表板项目');
assert(appSource.includes("type: 'table'"), '应用层缺少表格卡片快照');

console.log('[PASS] 仪表板导出预览、裁剪和聊天表格操作契约');
