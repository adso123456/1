import { renderToStaticMarkup } from 'react-dom/server';
import {
  WidgetAccessView,
  WidgetChat,
} from '../WidgetApp.js';
import {
  createMemorySessionStorage,
  resolveInitialSessionState,
} from '../hooks/useSSE.js';
import type { ChatMessage, SessionMeta } from '../types.js';

let passed = 0;
let failed = 0;

function test(name: string, callback: () => void) {
  try {
    callback();
    passed += 1;
    console.log(`[PASS] ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`[FAIL] ${name}:`, error);
  }
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

const secretMarkers = [
  'SECRET_SESSION_TITLE_917',
  'SECRET_SQL_SELECT_PRIVATE_917',
  'SECRET_MESSAGE_BODY_917',
  'SECRET_CHART_TITLE_917',
  'SECRET_DASHBOARD_917',
];

test('invalid、waiting、error 状态只渲染最小鉴权壳', () => {
  for (const status of ['invalid', 'waiting', 'error'] as const) {
    const html = renderToStaticMarkup(
      <WidgetAccessView embedContext={null} status={status} />,
    );
    for (const marker of secretMarkers) {
      assert(!html.includes(marker), `${status} 泄露了 ${marker}`);
    }
    for (const control of [
      '完整工作台',
      '完整查看',
      '添加到仪表板',
      '>SQL<',
      '导出',
      '选择会话',
      '选择数据源',
      '输入问题',
    ]) {
      assert(!html.includes(control), `${status} 挂载了聊天控件：${control}`);
    }
  }
});

test('protected 聊天使用 memory adapter，不读取或写入 localStorage', () => {
  let storageAccesses = 0;
  const originalStorage = globalThis.localStorage;
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem() {
        storageAccesses += 1;
        throw new Error('protected 不得读取 localStorage');
      },
      setItem() {
        storageAccesses += 1;
        throw new Error('protected 不得写入 localStorage');
      },
    },
  });
  try {
    const html = renderToStaticMarkup(
      <WidgetChat
        embedContext={{
          parentOrigin: 'http://127.0.0.1:5174',
          instanceId: 'storage-isolation-test',
          appId: 'water-platform-demo',
        }}
        requestOptions={{
          enabled: false,
          persistenceMode: 'memory',
        }}
        workspaceEnabled={false}
      />,
    );
    assert(storageAccesses === 0, `发生了 ${storageAccesses} 次存储访问`);
    assert(!html.includes('完整工作台'), 'protected 暴露了完整工作台');
    assert(!html.includes('完整查看'), 'protected 暴露了完整查看');
    assert(!html.includes('添加到仪表板'), 'protected 暴露了仪表板入口');
  } finally {
    if (originalStorage === undefined) {
      Reflect.deleteProperty(globalThis, 'localStorage');
    } else {
      Object.defineProperty(globalThis, 'localStorage', {
        configurable: true,
        value: originalStorage,
      });
    }
  }
});

test('memory 会话仅在同一 adapter 内保留，重建后为空', () => {
  const storage = createMemorySessionStorage();
  const initial = resolveInitialSessionState(undefined, storage);
  const message: ChatMessage = {
    id: 'u-memory',
    role: 'user',
    text: '仅内存消息',
    dataframes: [],
    charts: [],
    thinkingCollapsed: true,
    streaming: false,
  };
  const metadata: SessionMeta = {
    id: initial.id,
    title: '仅内存会话',
    createdAt: 1,
    updatedAt: 1,
    sourceId: 'allowed-source',
    sourceBound: true,
  };
  storage.saveSessions({ [initial.id]: [message] });
  storage.saveMeta({ [initial.id]: metadata });
  storage.saveCurrentId(initial.id);

  const restored = resolveInitialSessionState(undefined, storage);
  assert(restored.messages[0]?.text === '仅内存消息', '同一 iframe 内存未保留');
  assert(restored.sourceId === 'allowed-source', '内存 sourceId 未保留');

  const fresh = resolveInitialSessionState(
    undefined,
    createMemorySessionStorage(),
  );
  assert(fresh.messages.length === 0, '新鉴权实例恢复了旧内存消息');
  assert(fresh.sourceId === '', '新鉴权实例恢复了旧 sourceId');
  assert(fresh.sourceBound === false, '新鉴权实例恢复了旧绑定状态');
});

test('protected Widget 渲染应用配置并转义文本', () => {
  const html = renderToStaticMarkup(
    <WidgetChat
      embedContext={{
        parentOrigin: 'http://127.0.0.1:5174',
        instanceId: 'application-config-test',
        appId: 'water-platform-demo',
      }}
      requestOptions={{
        enabled: false,
        persistenceMode: 'memory',
      }}
      workspaceEnabled={false}
      applicationConfig={{
        app_id: 'application-config-test',
        name: '<b>水利助手</b>',
        theme: '#123456',
        logo_url: '',
        welcome: '应用欢迎语',
        welcome_description: '应用欢迎说明',
        show_history: false,
      }}
    />,
  );
  assert(html.includes('&lt;b&gt;水利助手&lt;/b&gt;'), '应用名称未按纯文本转义');
  assert(!html.includes('<b>水利助手</b>'), '应用名称被当作 HTML 渲染');
  assert(html.includes('应用欢迎语'), '应用欢迎语未显示');
  assert(html.includes('应用欢迎说明'), '应用欢迎说明未显示');
  assert(html.includes('--widget-theme:#123456'), '应用主题未应用');
  assert(!html.includes('选择会话'), 'show_history=false 时仍显示会话历史');
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
