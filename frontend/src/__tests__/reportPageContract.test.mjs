import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const components = read('src/components/ReportComponents.tsx');
const messageBubble = read('src/components/MessageBubble.tsx');
const useSSE = read('src/hooks/useSSE.ts');
const types = read('src/types.ts');
const app = read('src/App.tsx');
const sidebar = read('src/components/Sidebar.tsx');

const assertions = [
  ['快照生成 API', components.includes('/api/reports/water-quality/generate')],
  ['筛选项 API', components.includes('/api/reports/water-quality/options')],
  ['日报类型', components.includes("ReportType = 'daily' | 'monthly'")],
  ['月报类型', components.includes("ReportType = 'daily' | 'monthly'")],
  ['日期选择器', components.includes("type={reportType === 'daily' ? 'date' : 'month'}")],
  ['月报不显示回看天数', components.includes("reportType === 'daily' &&")],
  ['日报2至7日', components.includes('[2, 3, 4, 5, 6, 7]')],
  ['指标多选', components.includes('type="checkbox"')],
  ['真实频次选项', components.includes('按站点配置')],
  ['聊天共享参数组件', components.includes('<ReportParameterForm')],
  ['聊天配置卡', messageBubble.includes('<ReportConfigCard')],
  ['聊天结果卡', messageBubble.includes('<ReportResultCard')],
  ['结构化 SSE', useSSE.includes("rich.type === 'report_config'") && useSSE.includes("rich.type === 'report_result'")],
  ['会话持久化字段', types.includes('reportComponent?:')],
  ['统一预览弹窗', app.includes('<ReportPreviewModal')],
  ['预览使用 report_id URL', components.includes('src={result.preview_url}')],
  ['导出当前快照', components.includes('href={result.download_url}')],
  ['完整报告只在预览弹窗 iframe', components.includes('<iframe')],
  ['移除独立页面入口', !app.includes("currentView === 'reports'")],
  ['移除侧栏入口', !sidebar.includes("label: '日报月报'")],
];

let failures = 0;
for (const [name, passed] of assertions) {
  if (passed) console.log(`[PASS] ${name}`);
  else {
    failures += 1;
    console.error(`[FAIL] ${name}`);
  }
}
process.exitCode = failures ? 1 : 0;
