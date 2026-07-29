import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const reportPage = fs.readFileSync(
  path.join(root, 'src', 'components', 'ReportPage.tsx'),
  'utf8',
);
const app = fs.readFileSync(path.join(root, 'src', 'App.tsx'), 'utf8');
const sidebar = fs.readFileSync(
  path.join(root, 'src', 'components', 'Sidebar.tsx'),
  'utf8',
);

const assertions = [
  ['日报 API', reportPage.includes('/api/reports/water-quality/${reportType}')],
  ['日报类型', reportPage.includes('水质日报')],
  ['月报类型', reportPage.includes('水质月报')],
  ['日期选择器', reportPage.includes('type="date"')],
  ['月份选择器', reportPage.includes('type="month"')],
  ['生成和重新生成', reportPage.includes("summary ? '重新生成' : '生成报告'")],
  ['PDF 下载', reportPage.includes('下载 PDF')],
  ['预览 iframe', reportPage.includes('水质报告预览')],
  ['固定数据源', reportPage.includes('mysql-lzh-monitor')],
  ['筛选项 API', reportPage.includes('/api/reports/water-quality/options')],
  ['指标多选', reportPage.includes('type="checkbox"')],
  ['真实频次选项', reportPage.includes('按站点配置')],
  ['日报回看天数', reportPage.includes("value.set('recent_days'")],
  ['错误提示', reportPage.includes('report-message--error')],
  ['主工作台入口', app.includes("currentView === 'reports'")],
  ['侧栏入口', sidebar.includes("label: '日报月报'")],
];

let failures = 0;
for (const [name, passed] of assertions) {
  if (passed) {
    console.log(`[PASS] ${name}`);
  } else {
    failures += 1;
    console.error(`[FAIL] ${name}`);
  }
}

process.exitCode = failures ? 1 : 0;
