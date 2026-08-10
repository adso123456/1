import {
  buildWidgetUrl,
  buildWorkspaceUrl,
  readWorkspaceStorageNamespace,
  resolveWidgetAccessMode,
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

test('嵌入浮窗把隔离存储范围交接给完整工作台', () => {
  const scope = 'water-lan-demo:http://192.168.99.25:8081';
  const workspaceUrl = buildWorkspaceUrl(
    'http://192.168.99.25:5173',
    's_embed_session',
    scope,
  );
  assert(
    readWorkspaceStorageNamespace(workspaceUrl) === scope,
    '完整工作台未恢复嵌入存储范围',
  );
});

test('缺少或伪造嵌入上下文时 Widget 失败关闭', () => {
  const origin = 'http://127.0.0.1:5173';
  assert(
    resolveWidgetAccessMode(
      `${origin}/?mode=widget`,
      origin,
      true,
    ) === 'invalid',
    '直接 mode=widget 不应进入开发模式',
  );
  assert(
    resolveWidgetAccessMode(
      `${origin}/?mode=widget&parentOrigin=http://127.0.0.1:5174`,
      origin,
      true,
    ) === 'invalid',
    '缺少 instanceId 不应启用 Widget',
  );
  assert(
    resolveWidgetAccessMode(
      `${origin}/?mode=widget&instanceId=forged`,
      origin,
      true,
    ) === 'invalid',
    '缺少 parentOrigin 不应启用 Widget',
  );
});

test('跨域上下文始终受保护，同源开发入口必须显式且仅 DEV 生效', () => {
  const origin = 'http://127.0.0.1:5173';
  const protectedUrl = (
    `${origin}/?mode=widget`
    + '&parentOrigin=http://127.0.0.1:5174'
    + '&instanceId=water-agent-protected'
  );
  assert(
    resolveWidgetAccessMode(protectedUrl, origin, true) === 'protected',
    '合法跨域上下文未进入受保护模式',
  );
  const localUrl = buildWidgetUrl(
    origin,
    origin,
    'water-agent-local',
    true,
  );
  assert(
    resolveWidgetAccessMode(localUrl, origin, true)
    === 'local-development',
    '显式 DEV 同源入口未启用',
  );
  assert(
    resolveWidgetAccessMode(localUrl, origin, false) === 'protected',
    '生产构建同源嵌入应走父页面桥接（开发标记仅控制 DEV 直达模式）',
  );
  assert(
    resolveWidgetAccessMode(
      buildWidgetUrl(origin, origin, 'water-agent-forged'),
      origin,
      true,
    ) === 'protected',
    '同源查询参数合法时走父页面桥接（与跨域 protected 一致）',
  );
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
