import fs from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const loaderSource = fs.readFileSync(
  path.join(frontendRoot, 'public', 'water-agent-widget.js'),
  'utf8',
);

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.hidden = false;
    this.isConnected = false;
    this.parentNode = null;
    this.contentWindow = {};
    this.className = '';
    this.textContent = '';
  }

  appendChild(child) {
    child.parentNode = this;
    child.isConnected = this.isConnected;
    this.children.push(child);
    return child;
  }

  attachShadow() {
    this.shadowRoot = new FakeElement('shadow-root');
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

  dispatch(type, event = {}) {
    for (const handler of this.listeners.get(type) ?? []) handler(event);
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

const body = new FakeElement('body');
body.isConnected = true;
const documentListeners = new Map();
const document = {
  body,
  readyState: 'complete',
  currentScript: { dataset: { autoInit: 'false' } },
  createElement(tagName) {
    return new FakeElement(tagName);
  },
  addEventListener(type, handler) {
    documentListeners.set(type, handler);
  },
};

const windowListeners = new Map();
const window = {
  location: {
    origin: 'http://localhost:5173',
    href: 'http://localhost:5173/embed-demo',
  },
  addEventListener(type, handler) {
    windowListeners.set(type, handler);
  },
  removeEventListener(type, handler) {
    if (windowListeners.get(type) === handler) windowListeners.delete(type);
  },
};

vm.runInNewContext(loaderSource, {
  window,
  document,
  URL,
});

function findByClass(root, className) {
  if (root.className === className) return root;
  for (const child of root.children) {
    const match = findByClass(child, className);
    if (match) return match;
  }
  if (root.shadowRoot) return findByClass(root.shadowRoot, className);
  return null;
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

let passed = 0;
let failed = 0;
function test(name, callback) {
  try {
    callback();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`[FAIL] ${name}:`, error);
  }
}

test('初始化只创建一个机器人实例', () => {
  window.WaterAgentWidget.init({ agentUrl: 'http://localhost:5173' });
  window.WaterAgentWidget.init({ agentUrl: 'http://localhost:5173' });
  assert(body.children.length === 1, '重复创建了浮窗实例');
  assert(
    findByClass(body, 'water-agent-trigger'),
    '未创建机器人按钮',
  );
});

test('iframe 指向正确浮窗入口', () => {
  const iframe = findByClass(body, 'water-agent-frame');
  assert(
    iframe.src === 'http://localhost:5173/?mode=widget',
    `iframe URL 错误: ${iframe.src}`,
  );
});

test('点击、open 和 close 控制浮窗显示', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  const panel = findByClass(body, 'water-agent-panel');
  assert(panel.hidden === true, '初始状态应隐藏');
  trigger.dispatch('click');
  assert(panel.hidden === false, '点击后未打开');
  window.WaterAgentWidget.close();
  assert(panel.hidden === true, 'close 后未隐藏');
  window.WaterAgentWidget.open();
  assert(panel.hidden === false, 'open 后未显示');
});

test('destroy 清理按钮、iframe 和消息事件', () => {
  window.WaterAgentWidget.destroy();
  assert(body.children.length === 0, 'DOM 未清理');
  assert(!windowListeners.has('message'), '消息事件未清理');
});

const widgetAppSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);

test('浮窗复用 useSSE 与 ChatArea 且不加载仪表盘编辑区', () => {
  assert(widgetAppSource.includes('useSSE()'), '未复用 useSSE');
  assert(widgetAppSource.includes('<ChatArea'), '未复用 ChatArea');
  assert(widgetAppSource.includes('sessionList'), '未复用会话列表');
  assert(!widgetAppSource.includes('DashboardView'), '浮窗不应加载仪表盘');
});

test('窄屏时浮窗切换为近全屏布局', () => {
  assert(loaderSource.includes('@media(max-width:600px)'), '缺少窄屏断点');
  assert(
    loaderSource.includes('inset:8px 8px 78px'),
    '窄屏浮窗未设置视口内边距',
  );
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
