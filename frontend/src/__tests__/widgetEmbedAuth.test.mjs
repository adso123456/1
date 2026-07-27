import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const loaderSource = fs.readFileSync(
  path.join(frontendRoot, 'public', 'water-agent-widget.js'),
  'utf8',
);
const widgetAppSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);
const useSseSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'hooks', 'useSSE.ts'),
  'utf8',
);
const protocolSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'widgetMessageProtocol.ts'),
  'utf8',
);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class FakeElement {
  constructor(tagName, postedMessages) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.hidden = false;
    this.isConnected = false;
    this.parentNode = null;
    this.className = '';
    this.textContent = '';
    this.contentWindow = {
      postMessage(message, targetOrigin) {
        postedMessages.push({ message, targetOrigin });
      },
    };
  }

  appendChild(child) {
    child.parentNode = this;
    child.isConnected = this.isConnected;
    this.children.push(child);
    return child;
  }

  attachShadow() {
    this.shadowRoot = new FakeElement('shadow-root', []);
    return this.shadowRoot;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) ?? [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }

  removeEventListener(type, handler) {
    const handlers = this.listeners.get(type) ?? [];
    this.listeners.set(
      type,
      handlers.filter(candidate => candidate !== handler),
    );
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter(
        child => child !== this,
      );
    }
    this.isConnected = false;
  }
}

function createHarness() {
  const postedMessages = [];
  const body = new FakeElement('body', postedMessages);
  body.isConnected = true;
  const windowListeners = new Map();
  const document = {
    body,
    readyState: 'complete',
    currentScript: { dataset: { autoInit: 'false' } },
    createElement(tagName) {
      return new FakeElement(tagName, postedMessages);
    },
    addEventListener() {},
  };
  const window = {
    location: {
      origin: 'http://127.0.0.1:5174',
      href: 'http://127.0.0.1:5174/',
    },
    addEventListener(type, handler) {
      windowListeners.set(type, handler);
    },
    removeEventListener(type, handler) {
      if (windowListeners.get(type) === handler) windowListeners.delete(type);
    },
  };
  vm.runInNewContext(loaderSource, { window, document, URL });
  return { body, postedMessages, window, windowListeners };
}

function findByClass(root, className) {
  if (root.className === className) return root;
  for (const child of root.children) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  if (root.shadowRoot) return findByClass(root.shadowRoot, className);
  return null;
}

async function flushPromises() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setTimeout(resolve, 0));
}

let passed = 0;
let failed = 0;
async function test(name, callback) {
  try {
    await callback();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`[FAIL] ${name}:`, error);
  }
}

await test('Token 仅在合法 ready 后获取并通过受限 postMessage 传递', async () => {
  const harness = createHarness();
  let tokenRequests = 0;
  harness.window.WaterAgentWidget.init({
    agentUrl: 'http://127.0.0.1:5173',
    getToken: async () => {
      tokenRequests += 1;
      return { token: 'signed-test-token', expires_at: 4102444800 };
    },
  });
  const iframe = findByClass(harness.body, 'water-agent-frame');
  const iframeUrl = new URL(iframe.src);
  const instanceId = iframeUrl.searchParams.get('instanceId');
  const onMessage = harness.windowListeners.get('message');

  assert(tokenRequests === 0, 'iframe ready 前不应获取 Token');
  assert(!iframe.src.includes('signed-test-token'), 'Token 不得进入 iframe URL');

  onMessage({
    origin: 'http://attacker.example',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  await flushPromises();
  assert(tokenRequests === 0, '错误 Origin 不得触发 Token 获取');

  onMessage({
    origin: 'http://127.0.0.1:5173',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  await flushPromises();
  const authMessage = harness.postedMessages.find(
    item => item.message.type === 'water-agent-widget:auth',
  );
  assert(tokenRequests === 1, '合法 ready 应只获取一次 Token');
  assert(authMessage?.message.token === 'signed-test-token', '未发送签名 Token');
  assert(
    authMessage?.targetOrigin === 'http://127.0.0.1:5173',
    'Token postMessage 未限制到 Agent Origin',
  );
  harness.window.WaterAgentWidget.destroy();
  assert(!harness.windowListeners.has('message'), 'destroy 未清理消息监听器');
});

await test('Token 获取失败显示明确错误且不会发送鉴权消息', async () => {
  const harness = createHarness();
  harness.window.WaterAgentWidget.init({
    agentUrl: 'http://127.0.0.1:5173',
    getToken: async () => {
      throw new Error('host signing unavailable');
    },
  });
  const iframe = findByClass(harness.body, 'water-agent-frame');
  const loading = findByClass(harness.body, 'water-agent-loading');
  const instanceId = new URL(iframe.src).searchParams.get('instanceId');
  harness.windowListeners.get('message')({
    origin: 'http://127.0.0.1:5173',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  await flushPromises();
  assert(loading.hidden === false, 'Token 失败提示被隐藏');
  assert(loading.getAttribute('role') === 'alert', 'Token 失败提示缺少 alert 语义');
  assert(
    !harness.postedMessages.some(item => item.message.type === 'water-agent-widget:auth'),
    'Token 获取失败后不应发送鉴权消息',
  );
});

await test('嵌入请求在鉴权前禁用，鉴权后使用独立端点和请求头', () => {
  assert(widgetAppSource.includes("status: 'waiting'"), '缺少鉴权等待状态');
  assert(widgetAppSource.includes('enabled: embedAuth.status === \'authorized\''), '鉴权前未阻止请求');
  assert(widgetAppSource.includes("dataSourcesEndpoint: '/api/embed/data-sources'"), '未使用嵌入数据源端点');
  assert(widgetAppSource.includes("chatEndpoint: '/api/embed/vanna/v2/chat_sse'"), '未使用嵌入聊天端点');
  assert(widgetAppSource.includes('Authorization: `Bearer ${embedAuth.token}`'), '未发送 Bearer Token');
  assert(widgetAppSource.includes("'X-Water-Agent-Parent-Origin': embedContext!.parentOrigin"), '未发送宿主 Origin');
  assert(useSseSource.includes('if (!requestsEnabled) return;'), 'useSSE 未在鉴权前停止请求');
});

await test('鉴权消息校验 source、Origin、实例 ID 且 Token 不持久化或输出', () => {
  assert(protocolSource.includes('event.source === expectedSource'), '未校验消息 source');
  assert(protocolSource.includes('event.origin === context.parentOrigin'), '未校验消息 Origin');
  assert(protocolSource.includes('data.instanceId === context.instanceId'), '未校验消息实例 ID');
  assert(!loaderSource.includes('localStorage'), 'loader 不得持久化 Token');
  assert(!loaderSource.includes('sessionStorage'), 'loader 不得持久化 Token');
  assert(!loaderSource.includes('console.'), 'loader 不得输出 Token 或鉴权载荷');
  assert(loaderSource.includes("state.token = '';"), 'destroy/失败路径未清空内存 Token');
});

console.log(`\n嵌入鉴权前端测试：${passed} passed, ${failed} failed`);
if (failed > 0) process.exitCode = 1;
