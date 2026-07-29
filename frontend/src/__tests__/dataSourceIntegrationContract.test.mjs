import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const app = read('src/App.tsx');
const hook = read('src/hooks/useSSE.ts');
const page = read('src/components/DataSourcePage.tsx');
const state = read('src/dataSourceState.ts');
const widget = read('src/WidgetApp.tsx');
const vite = read('vite.config.ts');

const checks = [
  ['共享刷新入口', hook.includes('refreshDataSources')],
  ['管理操作通知主工作台', page.includes('onDataSourcesChanged')],
  ['新会话只显示 ready enabled', app.includes("source.status === 'ready' && source.enabled_for_chat")],
  ['发送门禁检查 ready', state.includes("source?.status === 'ready'")],
  ['发送门禁检查 enabled', state.includes('source.enabled_for_chat === true')],
  ['会话必须绑定', state.includes('sourceBound')],
  ['状态原因区分', ['draft', 'connected', 'metadata_ready', 'training_required', 'disabled', 'error'].every(value => state.includes(`${value}:`))],
  ['动态名称刷新历史会话', hook.includes('meta.sourceDisplayName = latestName')],
  ['Widget 使用授权摘要端点', widget.includes("dataSourcesEndpoint: '/api/embed/data-sources'")],
  ['Widget 无管理导航', !widget.includes("<DataSourcePage")],
  ['开发代理转发同源 Origin', vite.includes("Origin: 'http://localhost:8000'")],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`[FAIL] ${name}`);
  console.log(`[PASS] ${name}`);
}
