import { settleCancelledAssistantMessage } from '../chatRequestState.ts';
import type { ChatMessage } from '../types.ts';


function message(
  id: string,
  role: 'user' | 'assistant',
  overrides: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    id,
    role,
    text: '',
    dataframes: [],
    charts: [],
    thinkingCollapsed: true,
    streaming: role === 'assistant',
    ...overrides,
  };
}


const user = message('user', 'user', { text: '查询数据' });
const emptyAssistant = message('assistant-empty', 'assistant', {
  progressMessage: '正在生成查询语句',
  progressRequestId: 'request-empty',
});
const removed = settleCancelledAssistantMessage(
  [user, emptyAssistant],
  emptyAssistant.id,
);
if (removed.length !== 1 || removed[0] !== user) {
  throw new Error('取消无内容请求后必须删除空 assistant 占位消息');
}

const partialAssistant = message('assistant-partial', 'assistant', {
  text: '已经返回的部分内容',
  progressMessage: '正在整理答案',
  progressRequestId: 'request-partial',
});
const unrelatedAssistant = message('assistant-unrelated', 'assistant', {
  text: '其他请求',
});
const settled = settleCancelledAssistantMessage(
  [user, partialAssistant, unrelatedAssistant],
  partialAssistant.id,
);
const current = settled.find(item => item.id === partialAssistant.id);
const unrelated = settled.find(item => item.id === unrelatedAssistant.id);
if (
  !current
  || current.streaming
  || current.progressMessage !== undefined
  || current.progressRequestId !== undefined
  || current.text !== partialAssistant.text
) {
  throw new Error('取消已有内容请求后必须保留内容并清除 streaming/progress');
}
if (unrelated !== unrelatedAssistant || !unrelated.streaming) {
  throw new Error('取消一个请求不得改变其他 assistant 消息');
}

console.log('chat cancellation state behavior: PASS');
