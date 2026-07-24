import {
  buildChatRequestBody,
  canChangeSessionSource,
  normalizeDataSources,
  resolveSessionSourceId,
} from '../hooks/useSSE.js';

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

const sourcesPayload = [
  { source_id: 'source-a', database_type: 'postgresql' },
];

test('数据源列表可以加载并保留安全摘要', () => {
  const sources = normalizeDataSources(sourcesPayload);
  assert(sources.length === 1, '应加载一个数据源');
  assert(sources[0].source_id === 'source-a', 'source_id 不一致');
});

test('单数据源可以作为唯一自动选择候选', () => {
  const sources = normalizeDataSources(sourcesPayload);
  const selected = resolveSessionSourceId(undefined, sources);
  assert(selected === 'source-a', '单数据源未成为自动选择候选');
});

test('聊天请求包含 conversation_id 和 metadata.source_id', () => {
  const body = buildChatRequestBody('hello', 'session-a', 'source-a');
  assert(body.conversation_id === 'session-a', '缺少 conversation_id');
  assert(body.metadata.source_id === 'source-a', '缺少 source_id');
});

test('空会话允许选择数据源', () => {
  assert(
    canChangeSessionSource(false, '', 'source-b'),
    '空会话应允许选择数据源',
  );
});

test('已有消息的会话不能静默换源', () => {
  assert(
    !canChangeSessionSource(true, 'source-a', 'source-b'),
    '已有消息的会话不应允许换源',
  );
  assert(
    canChangeSessionSource(true, 'source-a', 'source-a'),
    '同一数据源应保持可用',
  );
});

test('会话元数据可分别恢复各自 sourceId', () => {
  const sources = normalizeDataSources([
    ...sourcesPayload,
    { source_id: 'source-b', database_type: 'offline' },
  ]);
  const metadata = {
    'session-a': { sourceId: 'source-a' },
    'session-b': { sourceId: 'source-b' },
  };
  assert(
    resolveSessionSourceId(metadata['session-a'].sourceId, sources)
      === 'source-a',
    '会话 A 恢复失败',
  );
  assert(
    resolveSessionSourceId(metadata['session-b'].sourceId, sources)
      === 'source-b',
    '会话 B 恢复失败',
  );
});

console.log(`total=${passed + failed} passed=${passed} failed=${failed}`);
if (failed > 0) throw new Error(`${failed} tests failed`);
