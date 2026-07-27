import {
  buildWidgetUrl,
  buildWorkspaceUrl,
  clearWorkspaceSessionParam,
  readWorkspaceSessionId,
} from '../appMode';
import { resolveInitialSessionState } from '../hooks/useSSE';
import {
  isWidgetMessage,
  postWidgetMessage,
  readWidgetEmbedContext,
} from '../widgetMessageProtocol';

let passed = 0;
let failed = 0;

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string): void {
  assert(
    actual === expected,
    `${message}: expected=${String(expected)} actual=${String(actual)}`,
  );
}

function deepEqual(actual: unknown, expected: unknown, message: string): void {
  equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

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

if (typeof localStorage === 'undefined') {
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    },
  });
}

test('跨域 widget URL 使用配置 Agent Origin 和受控父页面参数', () => {
  const withoutSlash = new URL(buildWidgetUrl(
    'http://127.0.0.1:5173',
    'http://127.0.0.1:5174',
    'widget-1',
  ));
  const withSlash = new URL(buildWidgetUrl(
    'http://127.0.0.1:5173/',
    'http://127.0.0.1:5174',
    'widget-1',
  ));

  equal(withoutSlash.origin, 'http://127.0.0.1:5173', '无尾斜杠 Origin 错误');
  equal(withSlash.origin, withoutSlash.origin, '尾斜杠 Origin 不一致');
  equal(withoutSlash.searchParams.get('mode'), 'widget', '缺少 widget mode');
  equal(
    withoutSlash.searchParams.get('parentOrigin'),
    'http://127.0.0.1:5174',
    '父页面 Origin 错误',
  );
  equal(withoutSlash.searchParams.get('instanceId'), 'widget-1', '实例 ID 错误');
});

test('Widget iframe 只接受匹配父 Origin、source、类型和实例 ID 的消息', () => {
  const context = readWidgetEmbedContext(
    'http://127.0.0.1:5173/?mode=widget'
      + '&parentOrigin=http%3A%2F%2F127.0.0.1%3A5174'
      + '&instanceId=widget-1',
  );
  assert(context, '合法嵌入上下文解析失败');
  const source = {} as Window;
  const validEvent = {
    source,
    origin: 'http://127.0.0.1:5174',
    data: {
      type: 'water-agent-widget:opened',
      instanceId: 'widget-1',
    },
  } as MessageEvent;

  equal(
    isWidgetMessage(
      validEvent,
      context,
      'water-agent-widget:opened',
      source,
    ),
    true,
    '合法消息被拒绝',
  );
  equal(
    isWidgetMessage(
      { ...validEvent, origin: 'http://evil.example' } as MessageEvent,
      context,
      'water-agent-widget:opened',
      source,
    ),
    false,
    '错误 Origin 消息被接受',
  );
  equal(
    isWidgetMessage(
      {
        ...validEvent,
        data: { ...validEvent.data, instanceId: 'widget-2' },
      } as MessageEvent,
      context,
      'water-agent-widget:opened',
      source,
    ),
    false,
    '错误实例 ID 消息被接受',
  );
});

test('iframe ready/minimize 使用明确的父页面 targetOrigin', () => {
  const context = {
    parentOrigin: 'http://127.0.0.1:5174',
    instanceId: 'widget-1',
  };
  const posted: Array<{ message: unknown; targetOrigin: string }> = [];
  const target = {
    postMessage(message: unknown, targetOrigin: string) {
      posted.push({ message, targetOrigin });
    },
  } as Window;

  postWidgetMessage(target, context, 'water-agent-widget:ready');
  postWidgetMessage(target, context, 'water-agent-widget:minimize');

  deepEqual(
    posted.map(item => item.targetOrigin),
    ['http://127.0.0.1:5174', 'http://127.0.0.1:5174'],
    'targetOrigin 不正确',
  );
  deepEqual(
    posted.map(item => item.message),
    [
      { type: 'water-agent-widget:ready', instanceId: 'widget-1' },
      { type: 'water-agent-widget:minimize', instanceId: 'widget-1' },
    ],
    '消息内容不正确',
  );
});

test('完整工作台 URL 只携带 session ID，读取后可清理参数', () => {
  const workspaceUrl = buildWorkspaceUrl(
    'http://127.0.0.1:5173',
    's_cross_origin',
  );
  equal(
    workspaceUrl,
    'http://127.0.0.1:5173/?session=s_cross_origin',
    '工作台 URL 错误',
  );
  equal(readWorkspaceSessionId(workspaceUrl), 's_cross_origin', 'session 读取失败');
  equal(
    clearWorkspaceSessionParam(`${workspaceUrl}&keep=1#chat`),
    '/?keep=1#chat',
    'session 参数未正确清理',
  );
  equal(workspaceUrl.includes('SELECT'), false, 'URL 泄露 SQL');
  equal(workspaceUrl.includes('message'), false, 'URL 泄露消息');
});

test('完整工作台从 Agent Origin 存储恢复消息、SQL、图表和数据源绑定', () => {
  localStorage.clear();
  const message = {
    id: 'assistant-1',
    role: 'assistant',
    text: '查询完成',
    dataframes: [{
      columns: ['区域', '数量'],
      data: [{ 区域: '夷陵区', 数量: 10 }],
      row_count: 1,
      column_count: 2,
    }],
    charts: [{
      id: 'chart-1',
      columns: ['区域', '数量'],
      rows: [{ 区域: '夷陵区', 数量: 10 }],
      spec: { type: 'bar', xField: '区域', yFields: ['数量'] },
      title: '区域数量',
      dataVersion: 1,
    }],
    thinkingCollapsed: true,
    streaming: false,
    sql: 'SELECT area_name, COUNT(*) FROM rs_outlet GROUP BY area_name',
  };
  localStorage.setItem(
    'water_qa_sessions',
    JSON.stringify({ s_cross_origin: [message] }),
  );
  localStorage.setItem(
    'water_qa_session_meta',
    JSON.stringify({
      s_cross_origin: {
        id: 's_cross_origin',
        title: '区域数量',
        createdAt: 1,
        updatedAt: 2,
        sourceId: 'postgresql-main',
        sourceBound: true,
      },
    }),
  );
  localStorage.setItem('water_qa_current_id', 'another-session');

  const restored = resolveInitialSessionState('s_cross_origin');

  equal(restored.id, 's_cross_origin', '会话 ID 未恢复');
  equal(restored.sourceId, 'postgresql-main', 'sourceId 未恢复');
  equal(restored.sourceBound, true, 'sourceBound 未恢复');
  equal(restored.messages[0].sql, message.sql, 'SQL 未恢复');
  equal(restored.messages[0].dataframes.length, 1, '表格未恢复');
  equal(restored.messages[0].charts.length, 1, '图表未恢复');
});

test('无效父 Origin、实例 ID 和未知 session 参数均被拒绝', () => {
  equal(
    readWidgetEmbedContext(
      'http://127.0.0.1:5173/?mode=widget'
        + '&parentOrigin=javascript%3Aalert(1)&instanceId=widget-1',
    ),
    null,
    '危险父 Origin 未拒绝',
  );
  equal(
    readWidgetEmbedContext(
      'http://127.0.0.1:5173/?mode=widget'
        + '&parentOrigin=http%3A%2F%2F127.0.0.1%3A5174'
        + '&instanceId=%3Cscript%3E',
    ),
    null,
    '危险实例 ID 未拒绝',
  );
  equal(
    readWorkspaceSessionId(
      'http://127.0.0.1:5173/?session=%3Cscript%3E',
    ),
    '',
    '危险 session 参数未拒绝',
  );
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
