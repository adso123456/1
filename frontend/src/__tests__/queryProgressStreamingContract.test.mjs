import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sse = fs.readFileSync(path.join(root, 'hooks', 'useSSE.ts'), 'utf8');
const bubble = fs.readFileSync(
  path.join(root, 'components', 'MessageBubble.tsx'),
  'utf8',
);
const widget = fs.readFileSync(path.join(root, 'WidgetApp.tsx'), 'utf8');

assert.match(sse, /rich\.type === 'progress'/);
assert.match(sse, /event\.request_id !== requestId/);
assert.match(sse, /rich\.type === 'text_delta'/);
assert.match(sse, /finalText \+= delta/);
assert.match(sse, /progressMessage: undefined/);
assert.match(bubble, /message\.progressMessage \|\| '正在准备问数…'/);
assert.match(widget, /useSSE\(undefined, requestOptions\)/);

console.log('query progress/streaming frontend contract: PASS');
