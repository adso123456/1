// questionSuggestionContract.test.mjs — 数据源专属推荐问题气泡 V1 契约测试
//
// 校验：ChatArea 不再硬编码业务问题，只负责接收并展示后端推荐问题；
// App 在新会话按会话绑定拉取推荐问题；Widget 保持兼容（默认空）。

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const root = process.cwd();
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const chatArea = read('src/components/ChatArea.tsx');
const app = read('src/App.tsx');
const widget = read('src/WidgetApp.tsx');
const types = read('src/types.ts');

const checks = [
  ['ChatArea 不再硬编码具体业务问题', !chatArea.includes("'夷陵区有哪些排污口'") && !chatArea.includes('const SUGGESTIONS')],
  ['ChatArea 导入 SuggestedQuestion 类型', chatArea.includes('SuggestedQuestion')],
  ['ChatArea 接收 suggestions prop 且默认空', chatArea.includes('suggestions = [],') || chatArea.includes('suggestions?: SuggestedQuestion[]')],
  ['ChatArea 按 prop 渲染推荐问题', chatArea.includes('suggestions.map(item =>') && chatArea.includes('onSend(item.text)')],
  ['ChatArea 无建议时不渲染气泡', chatArea.includes('suggestions.length > 0 &&')],
  ['App 按会话绑定拉取推荐问题', app.includes('/suggested-questions') && app.includes('encodeURIComponent(currentSessionId)')],
  ['App 仅在已绑定且无消息时拉取', app.includes('!currentSessionId || !sourceBound || messages.length > 0')],
  ['App 传 suggestions 给 ChatArea', app.includes('suggestions={suggestions}')],
  ['App 未绑定(404)视为空列表', app.includes('response.status === 404')],
  ['types 定义 SuggestedQuestion', types.includes('interface SuggestedQuestion') && types.includes('interface SuggestedQuestionsResponse')],
  ['Widget 不主动拉取推荐问题', !widget.includes('suggested-questions')],
  ['拉取地址只含会话 ID，不含可伪造的 source_id', app.includes('/api/conversations/') && !app.includes('suggested-questions?source_id=')],
];

for (const [name, ok] of checks) {
  if (!ok) throw new Error(`[FAIL] ${name}`);
  console.log(`[PASS] ${name}`);
}
