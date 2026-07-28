import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(testDir, '..');
const readSource = name => fs.readFileSync(
  path.join(sourceRoot, name),
  'utf8',
);

const appSource = readSource('App.tsx');
const sidebarSource = readSource(path.join('components', 'Sidebar.tsx'));
const adminSource = readSource('AdminApp.tsx');
const adminApiSource = readSource('adminApi.ts');
const mainSource = readSource('main.tsx');

assert(
  appSource.includes('<AssistantManagement embedded />')
    && appSource.includes(
      "display: currentView === 'assistant' ? 'block' : 'none'",
    ),
  '主工作台未保持挂载并直接展示小助手管理组件',
);
assert(
  sidebarSource.includes("key: 'assistant' as const")
    && sidebarSource.includes("label: '小助手'"),
  '主工作台侧边栏缺少小助手导航',
);
assert(
  adminSource.includes('export function AssistantManagement')
    && adminSource.includes('return <AssistantManagement />'),
  '兼容入口和主工作台未复用同一管理组件',
);
for (const forbidden of [
  '管理员 Token',
  '解锁管理页面',
  '锁定管理页面',
  'tokenInput',
  'unlockError',
]) {
  assert(
    !adminSource.includes(forbidden),
    `管理组件仍包含失效的 Token 会话逻辑: ${forbidden}`,
  );
}
assert(
  !adminApiSource.includes('Authorization')
    && !adminApiSource.includes('Bearer ${token}'),
  '前端管理请求仍发送 Authorization Token',
);
assert(
  mainSource.includes("mode === 'admin'")
    && mainSource.includes('<AdminApp />'),
  'mode=admin 兼容入口未保留',
);

console.log('assistant management integration: token-free shared view passed');
