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
const suggestion = read('src/components/DataSourceSuggestionCard.tsx');
const message = read('src/components/MessageBubble.tsx');
const presentation = read('src/dataSourcePresentation.ts');
const dialog = read('src/components/NewConversationSourceDialog.tsx');
const vite = read('vite.config.ts');

const checks = [
  ['共享刷新入口', hook.includes('refreshDataSources')],
  ['管理操作通知主工作台', page.includes('onDataSourcesChanged')],
  ['新会话只显示 ready enabled', app.includes("source.status === 'ready' && source.enabled_for_chat")],
  ['发送门禁检查 ready', state.includes("source?.status === 'ready'")],
  ['发送门禁检查 enabled', state.includes('source.enabled_for_chat === true')],
  ['会话必须绑定', state.includes('sourceBound')],
  ['状态原因区分', ['draft', 'connected', 'metadata_ready', 'training_required', 'disabled', 'error'].every(value => state.includes(`${value}:`))],
  ['启停按钮仅对应 ready/disabled', page.includes("source.status === 'ready'") && page.includes("source.status === 'disabled'") && !page.includes("source.status === 'disabled' ? 'enable' : 'disable'")],
  ['中间状态显示专用动作', ['继续配置', '选择表和字段', '生成问数资产', '刷新问数资产', '检查配置'].every(value => page.includes(value))],
  ['动态名称刷新历史会话', hook.includes('meta.sourceDisplayName = latestName')],
  [
    'Widget 通过父页面桥接读取授权摘要',
    widget.includes("dataSourcesEndpoint: 'widget-rpc:data-sources'")
      && widget.includes("rpcClient.request(")
      && widget.includes("'data-sources'"),
  ],
  ['Widget 无管理导航', !widget.includes("<DataSourcePage")],
  ['开发代理转发同源 Origin', vite.includes("Origin: 'http://localhost:8000'")],
  ['统一数据库类型格式', presentation.includes("return 'PostgreSQL'") && presentation.includes("return 'MySQL'")],
  ['统一中文状态格式', ['可问数', '未开启问数', '已停用', '未完成配置', '已连接', '待生成资产', '待刷新资产', '异常'].every(value => presentation.includes(value))],
  ['标题使用统一格式', app.includes('formatDatabaseType(currentSource.database_type)') && app.includes('formatDataSourceStatus(currentSource.status, currentSource.enabled_for_chat)')],
  ['选择弹窗使用统一格式', dialog.includes('formatDatabaseType(source.database_type)') && dialog.includes('formatDataSourceStatus(source.status, source.enabled_for_chat)')],
  ['建议卡渲染最新 display_name', suggestion.includes('latest?.status') && suggestion.includes('available[0].display_name')],
  ['建议卡不渲染内部 source_id', !suggestion.includes('>{source.source_id}<') && !widget.includes('>\n                  {source.source_id}')],
  ['单候选新建对话', suggestion.includes('在该数据源中新建对话')],
  ['多候选仅显示建议列表', suggestion.includes('available.map(source =>') && suggestion.includes('选择建议数据源')],
  ['目标失效中文提示', suggestion.includes('建议的数据源当前不可用，请重新选择可用数据源。')],
  ['建议前重新读取安全摘要', hook.includes('latestSources = normalizeDataSources(await response.json())')],
  ['原问题带入新会话', app.includes('setPendingQuestion(question)') && widget.includes('setPendingQuestion(question)')],
  ['旧消息和 reason 兜底清理', message.includes('sanitizeUserVisibleDataSourceText') && suggestion.includes('sanitizeUserVisibleDataSourceText(suggestion.reason)')],
  ['ready 卡片提供停用动作', page.includes('停用问数')],
  ['disabled 卡片提供启用动作', page.includes('启用问数')],
  ['编辑界面不显示内部 ID', !page.includes('内部 ID：')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`[FAIL] ${name}`);
  console.log(`[PASS] ${name}`);
}
