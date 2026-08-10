import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const widgetSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);
const rpcSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'widgetRpcClient.ts'),
  'utf8',
);
const widgetStyleSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'index.css'),
  'utf8',
).replace(/\r\n/g, '\n');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const checks = [
  [
    widgetSource.includes("rpcClient.request('application'"),
    'Widget 未通过父页面 RPC 读取应用配置',
  ],
  [
    widgetSource.includes("dataSourcesEndpoint: 'widget-rpc:data-sources'"),
    'Widget 数据源请求未切换到父页面 RPC',
  ],
  [
    widgetSource.includes("chatEndpoint: 'widget-rpc:chat'"),
    'Widget 聊天请求未切换到父页面 RPC',
  ],
  [
    widgetSource.includes("persistenceMode: 'local'")
      && widgetSource.includes('const persistenceNamespace = `${embedContext.appId}:${embedContext.parentOrigin}`')
      && widgetSource.includes('persistenceNamespace,'),
    '跨域 Widget 会话未按 appId 和父页面 Origin 隔离持久化',
  ],
  [
    widgetSource.includes('const dashboard = useDashboard(persistenceNamespace)')
      && widgetSource.includes('dashboard={dashboard}'),
    '跨域 Widget 未恢复隔离的仪表板能力',
  ],
  [
    widgetSource.includes('onClick={() => onNewSession()}'),
    '新建会话按钮不得把点击事件误传为 source_id',
  ],
  [
    widgetStyleSource.includes('.chat-area {\n  caret-color: transparent;')
      && widgetStyleSource.includes('.chat-area textarea,')
      && widgetStyleSource.includes('caret-color: auto;'),
    '聊天展示区应隐藏文本光标并保留真实输入控件光标',
  ],
  [
    widgetSource.includes('reportRequest={reportRequest}'),
    'Widget 报表请求未透传父页面 RPC',
  ],
  [
    !widgetSource.includes('Authorization')
      && !widgetSource.includes(['X-Water-Agent', 'Parent-Origin'].join('-')),
    'Widget 仍自行构造鉴权或父 Origin Header',
  ],
  [
    !widgetSource.includes('dangerouslySetInnerHTML'),
    '应用显示配置不得通过 HTML 注入渲染',
  ],
  [
    widgetSource.includes('onError={() => setLogoFailed(true)}'),
    'Logo 加载失败未回退默认图标',
  ],
  [
    widgetSource.includes('normalizeAssistantAppearance(candidate)')
      && widgetSource.includes('...DEFAULT_ASSISTANT_APPEARANCE'),
    'Widget 未复用统一外观默认值与规范化',
  ],
  [
    widgetSource.includes('postWidgetAppearanceMessage('),
    'Widget 成功加载配置后未发送外观快照',
  ],
  [
    rpcSource.includes('event.source === expectedSource')
      || rpcSource.includes('isWidgetRpcMessage('),
    'RPC 响应未复用严格消息校验',
  ],
];

for (const [passed, message] of checks) assert(passed, message);

console.log(`widget application config: ${checks.length} checks passed`);
