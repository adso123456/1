import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const widgetSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const checks = [
  [
    widgetSource.includes("fetch('/api/embed/application'"),
    'protected Widget 未使用 Embed 应用信息接口',
  ],
  [
    widgetSource.includes('Authorization: `Bearer ${token}`'),
    '应用信息请求未携带 Token',
  ],
  [
    widgetSource.includes(
      "'X-Water-Agent-Parent-Origin': embedContext.parentOrigin",
    ),
    '应用信息请求未携带父页面 Origin',
  ],
  [
    widgetSource.includes(
      'response.status === 401 || response.status === 403',
    ) && widgetSource.includes('onAuthorizationError();'),
    '应用信息鉴权失败未保持失败关闭',
  ],
  [
    widgetSource.includes(
      'setApplicationConfig(DEFAULT_WIDGET_APPLICATION_CONFIG)',
    ),
    '应用信息接口普通失败未回退安全默认显示',
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
    widgetSource.includes("persistenceMode: 'memory'"),
    '受保护 Widget 会话不再使用内存隔离',
  ],
];

for (const [passed, message] of checks) {
  assert(passed, message);
}

console.log(`widget application config: ${checks.length} checks passed`);
