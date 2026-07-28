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
const postedMessages = [];

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
    this.contentWindow = {
      postMessage(message, targetOrigin) {
        postedMessages.push({ message, targetOrigin });
      },
    };
    this.className = '';
    this.textContent = '';
    this.style = {
      setProperty(name, value) {
        this[name] = value;
      },
    };
    this.offsetWidth = 0;
    this.offsetHeight = 0;
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

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === 'src') this.src = '';
  }

  querySelector(selector) {
    if (!selector.startsWith('.')) return null;
    return findByClass(this, selector.slice(1));
  }

  getBoundingClientRect() {
    const left = Number.parseFloat(this.style.left) || 0;
    const top = Number.parseFloat(this.style.top) || 0;
    return {
      left,
      top,
      width: this.offsetWidth || (
        this.className === 'water-agent-trigger' ? 58 : 440
      ),
      height: this.offsetHeight || (
        this.className === 'water-agent-trigger' ? 58 : 700
      ),
    };
  }

  setPointerCapture() {}

  releasePointerCapture() {}

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
  innerWidth: 1280,
  innerHeight: 900,
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
  const iframeUrl = new URL(iframe.src);
  assert(
    iframeUrl.origin === 'http://localhost:5173'
      && iframeUrl.searchParams.get('mode') === 'widget',
    `iframe URL 错误: ${iframe.src}`,
  );
  assert(
    iframeUrl.searchParams.get('parentOrigin') === 'http://localhost:5173',
    'iframe 未收到受控父页面 Origin',
  );
  assert(
    /^water-agent-/.test(iframeUrl.searchParams.get('instanceId') || ''),
    'iframe 未收到实例 ID',
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

test('每次打开向 iframe 发送受目标来源约束的 resize 消息', () => {
  assert(postedMessages.length >= 2, '打开浮窗未发送 opened 消息');
  const latest = postedMessages.at(-1);
  assert(latest.message.type === 'water-agent-widget:opened', 'opened 消息类型错误');
  assert(latest.message.instanceId, 'opened 消息缺少实例 ID');
  assert(latest.targetOrigin === 'http://localhost:5173', 'opened 消息使用了非限定来源');
  assert(!loaderSource.includes("postMessage(\n        { type: 'water-agent-widget:opened' },\n        '*'"), 'opened 消息使用了 *');
});

test('loader 只接受匹配 Origin、source 和实例 ID 的 iframe 消息', () => {
  const iframe = findByClass(body, 'water-agent-frame');
  const panel = findByClass(body, 'water-agent-panel');
  const loading = findByClass(body, 'water-agent-loading');
  const iframeUrl = new URL(iframe.src);
  const instanceId = iframeUrl.searchParams.get('instanceId');
  const messageHandler = windowListeners.get('message');

  loading.hidden = false;
  messageHandler({
    origin: 'http://wrong.example',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  assert(loading.hidden === false, '错误 Origin 消息被接受');

  messageHandler({
    origin: 'http://localhost:5173',
    source: {},
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  assert(loading.hidden === false, '错误 source 消息被接受');

  messageHandler({
    origin: 'http://localhost:5173',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId: 'wrong-instance' },
  });
  assert(loading.hidden === false, '错误实例 ID 消息被接受');

  messageHandler({
    origin: 'http://localhost:5173',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:ready', instanceId },
  });
  assert(loading.hidden === true, '合法 ready 消息未生效');

  panel.hidden = false;
  messageHandler({
    origin: 'http://localhost:5173',
    source: iframe.contentWindow,
    data: { type: 'water-agent-widget:minimize', instanceId },
  });
  assert(panel.hidden === true, '合法 minimize 消息未收起浮窗');
});

function sendAppearance(appearance, overrides = {}) {
  const iframe = findByClass(body, 'water-agent-frame');
  const iframeUrl = new URL(iframe.src);
  windowListeners.get('message')({
    origin: 'http://localhost:5173',
    source: iframe.contentWindow,
    data: {
      type: 'water-agent-widget:appearance',
      instanceId: iframeUrl.searchParams.get('instanceId'),
      appearance,
      ...overrides,
    },
  });
}

const validAppearance = {
  theme: '#13579b',
  float_icon_url: 'https://example.test/icon.png',
  float_icon_draggable: true,
  float_x_anchor: 'left',
  float_x_offset: 30,
  float_y_anchor: 'top',
  float_y_offset: 40,
};

test('加载器应用严格外观快照并拒绝非法字段', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  sendAppearance(validAppearance);
  assert(
    trigger.style['--water-agent-theme'] === '#13579b',
    '主题色未应用',
  );
  assert(trigger.style.left === '30px', '水平锚点或偏移未应用');
  assert(trigger.style.top === '40px', '垂直锚点或偏移未应用');

  sendAppearance({ ...validAppearance, float_x_offset: 1001 });
  assert(trigger.style.left === '30px', '非法偏移覆盖了有效外观');
  sendAppearance({ ...validAppearance, token: 'must-not-pass' });
  assert(trigger.style.left === '30px', '含额外字段的消息被接受');
});

test('浮窗图标加载失败回退内置图标', () => {
  const image = findByClass(body, 'water-agent-trigger-image');
  const face = findByClass(body, 'water-agent-face');
  assert(image.src === validAppearance.float_icon_url, '图标 URL 未设置');
  image.onload();
  assert(image.style.display === 'block', '有效图片未显示');
  image.onerror();
  assert(image.style.display === 'none', '失败图片仍显示');
  assert(face.style.display === '', '内置图标未恢复');
});

test('四种锚点均作用于按钮和面板同侧定位', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  const panel = findByClass(body, 'water-agent-panel');
  for (const horizontal of ['left', 'right']) {
    for (const vertical of ['top', 'bottom']) {
      sendAppearance({
        ...validAppearance,
        float_icon_url: '',
        float_icon_draggable: false,
        float_x_anchor: horizontal,
        float_y_anchor: vertical,
      });
      assert(trigger.style[horizontal] === '30px', `${horizontal} 未应用`);
      assert(trigger.style[vertical] === '40px', `${vertical} 未应用`);
      window.WaterAgentWidget.open();
      assert(panel.style[horizontal] !== '', `面板未保持 ${horizontal}`);
      assert(panel.style[vertical] !== '', `面板未保持 ${vertical}`);
      window.WaterAgentWidget.close();
    }
  }
});

test('拖动超过阈值后吸附边角且不误触点击', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  const panel = findByClass(body, 'water-agent-panel');
  sendAppearance({
    ...validAppearance,
    float_icon_url: '',
    float_x_anchor: 'left',
    float_y_anchor: 'top',
  });
  panel.hidden = true;
  trigger.dispatch('pointerdown', {
    pointerId: 7,
    button: 0,
    clientX: 30,
    clientY: 40,
  });
  windowListeners.get('pointermove')({
    pointerId: 7,
    clientX: 1100,
    clientY: 800,
    preventDefault() {},
  });
  windowListeners.get('pointerup')({
    pointerId: 7,
    clientX: 1100,
    clientY: 800,
  });
  assert(trigger.style.left === '1214px', '未吸附到最近水平边');
  assert(trigger.style.top === '834px', '未吸附到最近垂直边');
  trigger.dispatch('click');
  assert(panel.hidden === true, '拖动结束误触发了打开');
  trigger.dispatch('click');
  assert(panel.hidden === false, '拖动后正常点击未恢复');
  assert(
    panel.style.right !== '' && panel.style.bottom !== '',
    '面板未使用页面拖动后的吸附锚点',
  );
  window.WaterAgentWidget.close();
});

test('appearance 重发只在持久化位置或拖动开关变化时复位', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  const draggedAppearance = {
    ...validAppearance,
    float_icon_url: '',
    float_x_anchor: 'left',
    float_y_anchor: 'top',
  };

  sendAppearance(draggedAppearance);
  assert(
    trigger.style.left === '1214px' && trigger.style.top === '834px',
    '完全相同 appearance 重发后拖动位置被复位',
  );

  const themedAppearance = {
    ...draggedAppearance,
    theme: '#abcdef',
  };
  sendAppearance(themedAppearance);
  assert(
    trigger.style.left === '1214px' && trigger.style.top === '834px',
    '仅修改 theme 后拖动位置被复位',
  );
  assert(
    trigger.style['--water-agent-theme'] === '#abcdef',
    '保留拖动位置时主题未更新',
  );

  const iconAppearance = {
    ...themedAppearance,
    float_icon_url: 'https://example.test/updated-icon.png',
  };
  sendAppearance(iconAppearance);
  assert(
    trigger.style.left === '1214px' && trigger.style.top === '834px',
    '仅修改 float_icon_url 后拖动位置被复位',
  );

  const movedPersistentAppearance = {
    ...iconAppearance,
    float_x_offset: 60,
    float_y_offset: 70,
  };
  sendAppearance(movedPersistentAppearance);
  assert(
    trigger.style.left === '60px' && trigger.style.top === '70px',
    '持久化位置变化后未使用新的数据库位置',
  );

  trigger.dispatch('pointerdown', {
    pointerId: 8,
    button: 0,
    clientX: 60,
    clientY: 70,
  });
  windowListeners.get('pointermove')({
    pointerId: 8,
    clientX: 1100,
    clientY: 800,
    preventDefault() {},
  });
  windowListeners.get('pointerup')({
    pointerId: 8,
    clientX: 1100,
    clientY: 800,
  });
  assert(
    trigger.style.left === '1214px' && trigger.style.top === '834px',
    '第二次拖动未形成页面内位置',
  );

  sendAppearance({
    ...movedPersistentAppearance,
    float_icon_draggable: false,
  });
  assert(
    trigger.style.left === '60px' && trigger.style.top === '70px',
    '禁用拖动后未恢复持久化位置',
  );
});

test('移动端忽略自定义偏移并禁用拖动', () => {
  const trigger = findByClass(body, 'water-agent-trigger');
  window.innerWidth = 600;
  sendAppearance(validAppearance);
  assert(
    trigger.style.left === ''
      && trigger.style.right === ''
      && trigger.style.top === ''
      && trigger.style.bottom === '',
    '移动端仍使用桌面自定义偏移',
  );
  trigger.dispatch('pointerdown', {
    pointerId: 9,
    button: 0,
    clientX: 20,
    clientY: 20,
  });
  windowListeners.get('pointermove')({
    pointerId: 9,
    clientX: 300,
    clientY: 300,
    preventDefault() {},
  });
  assert(trigger.style.left === '', '移动端仍可拖动');
  window.innerWidth = 1280;
  windowListeners.get('resize')();
});

test('iframe 加载失败时显示可理解提示', () => {
  const iframe = findByClass(body, 'water-agent-frame');
  const loading = findByClass(body, 'water-agent-loading');
  iframe.dispatch('error');
  assert(loading.hidden === false, '加载失败提示仍被隐藏');
  assert(
    loading.textContent.includes('Agent 前端已启动'),
    '加载失败提示不可理解',
  );
  assert(loading.getAttribute('role') === 'alert', '加载失败提示缺少 alert 语义');
});

test('重复初始化不会重复注册 message 监听器', () => {
  assert(windowListeners.has('message'), 'message 监听器缺失');
});

test('destroy 清理按钮、iframe 和消息事件', () => {
  window.WaterAgentWidget.destroy();
  assert(body.children.length === 0, 'DOM 未清理');
  assert(!windowListeners.has('message'), '消息事件未清理');
  for (const type of [
    'pointermove',
    'pointerup',
    'pointercancel',
    'resize',
  ]) {
    assert(!windowListeners.has(type), `${type} 事件未清理`);
  }
});

test('agentUrl 尾部有无斜杠均生成正确绝对 iframe 地址', () => {
  window.WaterAgentWidget.init({ agentUrl: 'http://agent.example:5173/' });
  const iframe = findByClass(body, 'water-agent-frame');
  assert(
    new URL(iframe.src).origin === 'http://agent.example:5173',
    `尾部斜杠处理错误: ${iframe.src}`,
  );
  window.WaterAgentWidget.destroy();
});

const widgetAppSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'WidgetApp.tsx'),
  'utf8',
);
const widgetProtocolSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'widgetMessageProtocol.ts'),
  'utf8',
);
const hostDemoSource = fs.readFileSync(
  path.join(frontendRoot, 'embed-host-demo', 'index.html'),
  'utf8',
);
const messageBubbleSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'components', 'MessageBubble.tsx'),
  'utf8',
);
const addDialogSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'components', 'AddToDashboardDialog.tsx'),
  'utf8',
);
const fullAppSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'App.tsx'),
  'utf8',
);
const chartViewSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'components', 'ChartView.tsx'),
  'utf8',
);
const indexCssSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'index.css'),
  'utf8',
);

test('浮窗复用 useSSE 与 ChatArea 且不加载仪表盘编辑区', () => {
  assert(widgetAppSource.includes('useSSE(undefined, requestOptions)'), '未复用 useSSE 嵌入请求配置');
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

test('浮窗透传 compact 并恢复添加到仪表板回调', () => {
  assert(
    messageBubbleSource.includes('compact={compact}'),
    'compact 未传递到 ChartView',
  );
  assert(
    widgetAppSource.includes('dashboard ? handleRequestAddToDashboard : undefined'),
    'ChatArea 未收到 onAddToDashboard',
  );
  assert(widgetAppSource.includes('<AddToDashboardDialog'), '未复用添加弹窗');
});

test('浮窗支持已有、新建仪表板并明确提示写入结果', () => {
  assert(addDialogSource.includes("mode === 'existing'"), '缺少已有模式');
  assert(addDialogSource.includes("mode === 'new'"), '缺少新建模式');
  assert(widgetAppSource.includes('addItemsToDashboard('), '缺少已有仪表板写入');
  assert(widgetAppSource.includes('createDashboardWithItems('), '缺少新建仪表板写入');
  assert(widgetAppSource.includes('添加失败，localStorage'), '写入失败未提示');
  assert(widgetAppSource.includes('已添加到仪表板'), '写入成功未提示');
});

test('浮窗与完整工作台复用同一 useDashboard 存储', () => {
  assert(widgetAppSource.includes("from './hooks/useDashboard'"), '浮窗未使用共享 Hook');
  assert(fullAppSource.includes("from './hooks/useDashboard'"), '完整工作台未使用共享 Hook');
});

test('compact 图表使用独立高度且说明紧跟图表，普通图表仍为 350px', () => {
  assert(
    chartViewSource.includes('height: compact ? compactChartHeight : 350'),
    'compact 与普通图表高度未分离',
  );
  assert(
    chartViewSource.includes('marginTop: 10'),
    '图表说明没有紧跟 ECharts 容器',
  );
});

test('compact 图表监听真实宽度并在浮窗重新打开或变宽后刷新', () => {
  assert(
    chartViewSource.includes('const [compactWidth, setCompactWidth] = useState(0)'),
    '未记录 compact 实际宽度',
  );
  assert(
    chartViewSource.includes('container.getBoundingClientRect().width'),
    '未读取 ChartView 外层容器宽度',
  );
  assert(
    chartViewSource.includes('observer.observe(container)'),
    'ResizeObserver 未监听 ChartView 外层容器',
  );
  assert(
    chartViewSource.includes("window.addEventListener('water-agent-widget:opened'"),
    'ResizeObserver 未同步 compact 宽度',
  );
  assert(
    chartViewSource.includes('width: compactWidth || 400'),
    'compact option 未使用实测宽度',
  );
});

test('WidgetApp 校验 opened 消息来源、实例 ID 且只注册一次监听器', () => {
  assert(widgetAppSource.includes('isWidgetMessage('), 'WidgetApp 未使用受控消息校验');
  assert(widgetProtocolSource.includes('event.source === expectedSource'), '未校验 opened 消息 source');
  assert(widgetProtocolSource.includes('event.origin === context.parentOrigin'), '未校验 opened 消息 origin');
  assert(widgetProtocolSource.includes('data.instanceId === context.instanceId'), '未校验 opened 实例 ID');
  assert(widgetAppSource.includes("removeEventListener('message', handleWidgetMessage)"), 'widget 消息监听器未清理');
});

test('5174 静态宿主页只跨域加载脚本，不直接访问 API 或 Agent 存储', () => {
  assert(
    hostDemoSource.includes('src="http://127.0.0.1:5173/water-agent-widget.js"'),
    '宿主页未通过绝对地址加载 5173 脚本',
  );
  assert(
    hostDemoSource.includes("agentUrl: 'http://127.0.0.1:5173'"),
    '宿主页未配置 Agent 地址',
  );
  assert(
    !hostDemoSource.includes('/api/data-sources') && !hostDemoSource.includes('/api/vanna/'),
    '宿主页除签发 Token 外不应直接访问 Agent API',
  );
  assert(hostDemoSource.includes('/api/embed-token'), '宿主页应从宿主后端获取嵌入 Token');
  assert(!hostDemoSource.includes('localStorage'), '宿主页不应读取 Agent localStorage');
});

test('本板块消息全部使用明确 targetOrigin，不使用通配符', () => {
  assert(!loaderSource.includes("postMessage(message, '*')"), 'loader 使用了通配符 Origin');
  assert(!widgetProtocolSource.includes("postMessage(\n    { type, instanceId: context.instanceId },\n    '*'"), 'iframe 使用了通配符 Origin');
  assert(widgetProtocolSource.includes('context.parentOrigin'), 'iframe 未使用父页面明确 Origin');
});

test('compact 提供紧凑标题、饼图替代状态和完整查看入口', () => {
  assert(chartViewSource.includes('className="compact-chart-title"'), '缺少紧凑外层标题');
  assert(chartViewSource.includes('className="compact-chart-unavailable"'), '缺少饼图替代状态');
  assert(chartViewSource.includes('切换为横向柱状图'), '缺少横向柱图快捷切换');
  assert(chartViewSource.includes('完整查看'), '工具栏缺少完整查看');
  assert(chartViewSource.includes('在完整工作台查看'), '替代状态缺少完整工作台入口');
  assert(messageBubbleSource.includes('workspaceUrl={workspaceUrl}'), '完整查看地址未传递到 ChartView');
  assert(indexCssSource.includes('.compact-chart-title'), '缺少紧凑标题样式');
  assert(indexCssSource.includes('text-overflow: ellipsis'), '紧凑标题未启用单行截断');
});

test('Toast 脱离 flex 流并定位在浮窗可见区域', () => {
  assert(/\.widget-shell \{\r?\n  position: relative;/.test(indexCssSource), '浮窗根容器未建立定位上下文');
  assert(/\.widget-toast \{\r?\n  position: absolute;/.test(indexCssSource), 'Toast 仍参与正常 flex 布局');
  assert(indexCssSource.includes('top: 108px;'), 'Toast 未位于会话栏下方');
  assert(indexCssSource.includes('z-index: 1200;'), 'Toast 层级不足');
});

test('成功 Toast 2.5 秒自动关闭，失败 Toast 保持到手动关闭', () => {
  assert(widgetAppSource.includes('if (!notice?.ok) return;'), '失败 Toast 被错误设置为自动关闭');
  assert(widgetAppSource.includes('window.setTimeout(() => setNotice(null), 2500)'), '成功 Toast 缺少 2.5 秒自动关闭');
  assert(widgetAppSource.includes('window.clearTimeout(timer)'), '成功 Toast 定时器未清理');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
