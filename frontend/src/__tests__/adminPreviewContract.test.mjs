import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(currentDir, '..');

function loadCommonJs(relativePath) {
  const source = fs.readFileSync(path.join(srcRoot, relativePath), 'utf8');
  const javascript = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(javascript, {
    module,
    exports: module.exports,
    URL,
    URLSearchParams,
  });
  return { exports: module.exports, source };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const appMode = loadCommonJs('appMode.ts');
const protocol = loadCommonJs('widgetMessageProtocol.ts');
const previewSource = fs.readFileSync(
  path.join(
    srcRoot,
    'components',
    'admin',
    'AssistantPreviewDialog.tsx',
  ),
  'utf8',
);

const previewUrl = appMode.exports.buildAdminPreviewWidgetUrl(
  'http://localhost:5173',
  'http://localhost:5173',
  'preview-1',
);
assert(
  appMode.exports.resolveWidgetAccessMode(
    previewUrl,
    'http://localhost:5173',
    false,
  ) === 'protected',
  '同源管理预览未进入 protected Gate',
);
assert(
  appMode.exports.resolveWidgetAccessMode(
    'http://localhost:5173/?mode=widget'
      + '&parentOrigin=http%3A%2F%2Flocalhost%3A5173'
      + '&instanceId=preview-1',
    'http://localhost:5173',
    true,
  ) === 'invalid',
  '无标记同源入口不应自动获得 Widget 访问模式',
);
assert(
  appMode.exports.resolveWidgetAccessMode(
    'http://localhost:5173/?mode=widget'
      + '&parentOrigin=http%3A%2F%2Flocalhost%3A5173'
      + '&instanceId=preview-1'
      + '&devWidget=project-embed-demo',
    'http://localhost:5173',
    true,
  ) === 'local-development',
  '原 devWidget 行为回归',
);
assert(!/[?&](token|secret|authorization)=/i.test(previewUrl), '预览 URL 含凭据');

const posted = [];
const target = {
  postMessage(message, targetOrigin) {
    posted.push({ message, targetOrigin });
  },
};
const context = {
  parentOrigin: 'http://localhost:5173',
  instanceId: 'preview-1',
};
protocol.exports.postWidgetAuthMessage(
  target,
  context,
  'short-preview-token',
  123,
);
assert(
  posted[0].targetOrigin === context.parentOrigin
    && posted[0].message.instanceId === context.instanceId,
  'auth 未发送到精确 targetOrigin/instanceId',
);
assert(
  protocol.exports.isWidgetMessage(
    {
      source: target,
      origin: context.parentOrigin,
      data: {
        type: 'water-agent-widget:ready',
        instanceId: context.instanceId,
      },
    },
    context,
    'water-agent-widget:ready',
    target,
  ),
  '合法消息未通过 source/origin/instance 校验',
);
assert(
  !protocol.exports.isWidgetMessage(
    {
      source: {},
      origin: context.parentOrigin,
      data: {
        type: 'water-agent-widget:ready',
        instanceId: context.instanceId,
      },
    },
    context,
    'water-agent-widget:ready',
    target,
  ),
  '错误 source 消息未拒绝',
);

for (const required of [
  'requestRef.current',
  'requestAuthorization(current, frameWindow);',
  'active?.id !== current.id',
  'target !== frameWindow',
  'invalidateSession();',
  'requestRef.current?.controller.abort();',
  'createSession(nextSessionIdRef.current++)',
]) {
  assert(previewSource.includes(required), `预览所有权实现缺少: ${required}`);
}
assert(!previewSource.includes("postMessage('*'"), '预览使用了通配 targetOrigin');

console.log('admin preview contract: all checks passed');
