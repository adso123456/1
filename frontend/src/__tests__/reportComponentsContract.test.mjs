import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = relative => fs.readFileSync(path.join(root, relative), 'utf8');
const panel = read('src/components/ReportComposerPanel.tsx');
const panelCss = read('src/components/ReportComposerPanel.css');
const components = read('src/components/ReportComponents.tsx');
const chatArea = read('src/components/ChatArea.tsx');
const messageBubble = read('src/components/MessageBubble.tsx');
const hook = read('src/hooks/useSSE.ts');
const widget = read('src/WidgetApp.tsx');
const reportState = read('src/reportConfigState.ts');

const reportConfigBranch = hook.slice(
  hook.indexOf("rich.type === 'report_config'"),
  hook.indexOf("rich.type === 'report_result'"),
);

const checks = [
  ['report_config 不进入消息列表', reportConfigBranch.includes('setPendingReportConfigs') && reportConfigBranch.includes('prev.filter')],
  ['配置使用独立持久化键', hook.includes("PENDING_REPORTS_KEY = 'water_qa_pending_reports'")],
  ['配置按会话索引', hook.includes('[conversation_id || currentSessionId]')],
  ['刷新恢复待确认配置', hook.includes('storage.loadPendingReports()')],
  ['切换会话读取当前配置', hook.includes('pendingReportConfigs[currentSessionId] ?? null')],
  ['删除会话清理配置', hook.includes('delete next[id]')],
  ['数据源停用后安全关闭配置', hook.includes('dataSourcesLoaded') && hook.includes('未确认的配置已安全关闭')],
  ['普通消息关闭旧配置', hook.includes('delete next[currentSessionId]')],
  ['输入框上方渲染面板', chatArea.indexOf('<ReportComposerPanel') < chatArea.indexOf('<textarea')],
  ['日报日期与月报月份切换', panel.includes("type={config.report_type === 'daily' ? 'date' : 'month'}")],
  ['泛化报表类型选择', panel.includes('config.report_type_selectable') && panel.includes('水质日报') && panel.includes('水质月报')],
  ['月报隐藏回看范围', panel.includes("config.report_type === 'daily' &&")],
  ['回看范围来自2至7日配置', panel.includes('config.available_recent_days.map')],
  ['指标摘要与加号计数', panel.includes('report-summary__chip') && panel.includes('+{selectedOptions.length - 1}')],
  ['摘要支持移除指标', panel.includes('toggleIndicator(item.code)')],
  ['指标面板向上展开', panelCss.includes('bottom: calc(100% + 7px)')],
  ['指标列表可滚动', panelCss.includes('overflow-y: auto')],
  ['频次只来自真实选项', panel.includes('indicator.frequencies.map')],
  ['未选择指标禁用频次', panel.includes('disabled={loading || !checked}')],
  ['至少选择一个指标', panel.includes('请至少选择一个应测指标')],
  ['取消不生成', panel.includes('onClick={onCancel}')],
  ['生成中禁止重复提交', panel.includes("loading ? '生成中…' : '生成预览'") && panel.includes('disabled={loading')],
  ['失败保留配置并显示错误', panel.includes('config.error') && panel.includes('onChange({')],
  ['成功插入正式结果', hook.includes('appendReportResult') && hook.includes("type: 'report_result'")],
  ['修改配置重新生成', components.includes('修改配置重新生成') && reportState.includes('configFromReportResult')],
  ['旧 report_config 消息兼容', messageBubble.includes('<ReportConfigCard')],
  ['Widget 复用配置面板和结果预览', widget.includes('pendingReportConfig={pendingReportConfig}') && widget.includes('<ReportPreviewModal')],
  ['Widget 不显示数据源管理入口', !widget.includes('<DataSourcePage')],
  ['配置面板不渲染 source_id', !panel.includes('config.source_id')],
  ['移动端使用底部抽屉', panelCss.includes('position: fixed') && panelCss.includes('bottom: 0')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`[FAIL] ${name}`);
  console.log(`[PASS] ${name}`);
}
