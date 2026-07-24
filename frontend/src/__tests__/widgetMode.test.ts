import {
  buildWidgetUrl,
  buildWorkspaceUrl,
  resolveApplicationMode,
} from '../appMode.js';

let passed = 0;
let failed = 0;

function test(name: string, callback: () => void): void {
  try {
    callback();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`[FAIL] ${name}:`, error);
  }
}

function assert(condition: boolean, message: string): void {
  if (!condition) throw new Error(message);
}

test('固定 /embed-demo 路径进入模拟目标网站', () => {
  assert(
    resolveApplicationMode('/embed-demo', '') === 'embed-demo',
    '未识别模拟网站入口',
  );
});

test('mode=widget 进入紧凑浮窗页', () => {
  assert(
    resolveApplicationMode('/', '?mode=widget') === 'widget',
    '未识别浮窗入口',
  );
});

test('普通路径保持完整工作台', () => {
  assert(
    resolveApplicationMode('/', '') === 'workspace',
    '普通工作台入口被改变',
  );
});

test('浮窗与完整工作台 URL 保持同源', () => {
  const agentUrl = 'http://localhost:5173/embed-demo';
  assert(
    buildWidgetUrl(agentUrl) === 'http://localhost:5173/?mode=widget',
    '浮窗 URL 不正确',
  );
  assert(
    buildWorkspaceUrl(agentUrl) === 'http://localhost:5173/',
    '完整工作台 URL 不正确',
  );
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
