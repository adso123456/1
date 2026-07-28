import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const protocol = await import(
  pathToFileURL(
    path.join(frontendRoot, 'src', 'widgetMessageProtocol.ts'),
  )
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const calls = [];
const target = {
  postMessage(message, targetOrigin) {
    calls.push({ message, targetOrigin });
  },
};
const context = {
  parentOrigin: 'https://host.example',
  instanceId: 'appearance-instance',
};
protocol.postWidgetAppearanceMessage(target, context, {
  theme: '#1677ff',
  float_icon_url: 'https://agent.example/icon.png',
  float_icon_draggable: true,
  float_x_anchor: 'right',
  float_x_offset: 24,
  float_y_anchor: 'bottom',
  float_y_offset: 24,
});

assert(calls.length === 1, '外观快照发送次数错误');
assert(
  calls[0].targetOrigin === context.parentOrigin,
  '外观消息未使用精确 targetOrigin',
);
assert(
  calls[0].message.type === 'water-agent-widget:appearance'
    && calls[0].message.instanceId === context.instanceId,
  '外观消息类型或实例 ID 错误',
);
assert(
  Object.keys(calls[0].message.appearance).sort().join(',') === [
    'float_icon_draggable',
    'float_icon_url',
    'float_x_anchor',
    'float_x_offset',
    'float_y_anchor',
    'float_y_offset',
    'theme',
  ].join(','),
  '外观消息字段不是严格白名单',
);
const serialized = JSON.stringify(calls[0]);
for (const forbidden of [
  'token',
  'secret',
  'authorization',
  'app_id',
  'subject',
  'source_id',
]) {
  assert(
    !serialized.toLowerCase().includes(forbidden),
    `外观消息泄漏敏感字段: ${forbidden}`,
  );
}

console.log('widget appearance protocol: exact-origin credential-free snapshot passed');
