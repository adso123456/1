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
const widgetSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);
const protocolSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'widgetMessageProtocol.ts'),
  'utf8',
);
const rpcSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'widgetRpcClient.ts'),
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
    this.style = { setProperty(name, value) { this[name] = value; } };
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

function findByClass(root, className) {
  if (root.className === className) return root;
  for (const child of root.children) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  if (root.shadowRoot) return findByClass(root.shadowRoot, className);
  return null;
}

function createHarness() {
  const postedMessages = [];
  const fetchCalls = [];
  const aborted = [];
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
    innerWidth: 1280,
    innerHeight: 900,
    location: {
      origin: 'http://127.0.0.1:15174',
      href: 'http://127.0.0.1:15174/',
    },
    addEventListener(type, handler) {
      windowListeners.set(type, handler);
    },
    removeEventListener(type, handler) {
      if (windowListeners.get(type) === handler) {
        windowListeners.delete(type);
      }
    },
  };
  const fetch = async (url, init) => {
    fetchCalls.push({ url, init });
    if (url.endsWith('/application')) {
      return new Response(JSON.stringify({ app_id: 'water-platform-demo' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.endsWith('/data-sources')) {
      return new Response(JSON.stringify([{
        source_id: 'postgresql-main',
        database_type: 'postgresql',
      }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.endsWith('/chat_sse')) {
      const bodyValue = JSON.parse(init.body);
      if (bodyValue.message === 'slow') {
        return await new Promise((_resolve, reject) => {
          init.signal.addEventListener('abort', () => {
            aborted.push(true);
            reject(new DOMException('aborted', 'AbortError'));
          });
        });
      }
      const chunks = [
        'data: {"type":"text","data":{"content":"one"}}\n\n',
        'data: [DONE]\n\n',
      ];
      return new Response(new ReadableStream({
        start(controller) {
          for (const chunk of chunks) {
            controller.enqueue(new TextEncoder().encode(chunk));
          }
          controller.close();
        },
      }), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    }
    throw new Error(`unexpected URL: ${url}`);
  };
  vm.runInNewContext(loaderSource, {
    window,
    document,
    URL,
    fetch,
    AbortController,
    DOMException,
    Response,
    ReadableStream,
    TextDecoder,
    setTimeout,
    clearTimeout,
  });
  return {
    aborted,
    body,
    fetchCalls,
    postedMessages,
    window,
    windowListeners,
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise(resolve => setTimeout(resolve, 0));
}

const harness = createHarness();
harness.window.WaterAgentWidget.init({
  agentUrl: 'http://127.0.0.1:15175',
  apiUrl: 'http://127.0.0.1:18012',
  appId: 'water-platform-demo',
});
const iframe = findByClass(harness.body, 'water-agent-frame');
const iframeUrl = new URL(iframe.src);
const instanceId = iframeUrl.searchParams.get('instanceId');
const onMessage = harness.windowListeners.get('message');

assert(
  iframeUrl.searchParams.get('appId') === 'water-platform-demo',
  '公开 appId 未进入 iframe 上下文',
);
assert(
  !iframe.src.match(/secret|jwt|bearer/i),
  'iframe URL 出现凭据字段',
);

onMessage({
  origin: 'http://attacker.example',
  source: iframe.contentWindow,
  data: {
    type: 'water-agent-widget:rpc-request',
    instanceId,
    requestId: 'rpc-attacker',
    operation: 'application',
  },
});
await flush();
assert(harness.fetchCalls.length === 0, '错误 Origin 触发了父页面请求');

onMessage({
  origin: 'http://127.0.0.1:15175',
  source: iframe.contentWindow,
  data: {
    type: 'water-agent-widget:rpc-request',
    instanceId,
    requestId: 'rpc-application',
    operation: 'application',
  },
});
await flush();
assert(
  harness.fetchCalls[0].url
    === 'http://127.0.0.1:18012/api/embed/apps/water-platform-demo/application',
  '应用配置请求 URL 未使用路径 appId',
);
assert(
  harness.fetchCalls[0].init.credentials === 'omit',
  '父页面请求不应启用 credentials',
);
assert(
  !JSON.stringify(harness.fetchCalls[0].init.headers)
    .match(/authorization|bearer|token|secret/i),
  '父页面请求携带了凭据 Header',
);
const applicationReply = harness.postedMessages.find(
  item => item.message.requestId === 'rpc-application',
);
assert(applicationReply?.targetOrigin === 'http://127.0.0.1:15175',
  'RPC 响应未限制到 Widget Origin');

onMessage({
  origin: 'http://127.0.0.1:15175',
  source: iframe.contentWindow,
  data: {
    type: 'water-agent-widget:rpc-request',
    instanceId,
    requestId: 'rpc-chat',
    operation: 'chat',
    payload: {
      message: 'hello',
      conversation_id: 'conversation',
      metadata: { source_id: 'postgresql-main' },
    },
  },
});
await flush();
const chatMessages = harness.postedMessages.filter(
  item => item.message.requestId === 'rpc-chat',
);
assert(
  chatMessages.some(item =>
    item.message.type === 'water-agent-widget:rpc-response'),
  'SSE 未先返回响应状态',
);
assert(
  chatMessages.filter(item =>
    item.message.type === 'water-agent-widget:rpc-chunk').length === 2,
  'SSE 分片未逐段转发',
);
assert(
  chatMessages.at(-1)?.message.type === 'water-agent-widget:rpc-end',
  'SSE 未发送结束消息',
);

onMessage({
  origin: 'http://127.0.0.1:15175',
  source: iframe.contentWindow,
  data: {
    type: 'water-agent-widget:rpc-request',
    instanceId,
    requestId: 'rpc-cancel',
    operation: 'chat',
    payload: {
      message: 'slow',
      conversation_id: 'conversation',
      metadata: { source_id: 'postgresql-main' },
    },
  },
});
await flush();
onMessage({
  origin: 'http://127.0.0.1:15175',
  source: iframe.contentWindow,
  data: {
    type: 'water-agent-widget:rpc-cancel',
    instanceId,
    requestId: 'rpc-cancel',
  },
});
await flush();
assert(harness.aborted.length === 1, '取消消息未中止父页面 fetch');

for (const source of [loaderSource, widgetSource, protocolSource, rpcSource]) {
  assert(!source.match(
    new RegExp([
      'ProtectedWidget' + 'Gate',
      'water-agent-widget:' + 'auth',
      'auth-' + 'required',
      'get' + 'Token',
      'Bear' + 'er',
      ['X-Water-Agent', 'Parent-Origin'].join('-'),
    ].join('|')),
  ), 'Embed 前端仍有旧凭据链路');
}
assert(!loaderSource.includes('localStorage'), 'Loader 不得写 localStorage');
assert(!loaderSource.includes('sessionStorage'), 'Loader 不得写 sessionStorage');
assert(!loaderSource.includes('console.'), 'Loader 不得输出请求数据');
assert(protocolSource.includes('event.source === expectedSource'),
  '消息协议未校验 event.source');
assert(protocolSource.includes('event.origin === context.parentOrigin'),
  '消息协议未校验 event.origin');
assert(protocolSource.includes('data.instanceId === context.instanceId'),
  '消息协议未校验 instanceId');
assert(protocolSource.includes("typeof data.requestId === 'string'"),
  'RPC 客户端未校验 requestId');

harness.window.WaterAgentWidget.destroy();
assert(!harness.windowListeners.has('message'), 'destroy 未清理消息监听器');

console.log('widget parent RPC/SSE/cancel: all checks passed');
