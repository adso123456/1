import type { ChatMessage } from './types';


function hasAssistantContent(message: ChatMessage): boolean {
  return Boolean(
    message.text.trim()
    || message.dataframes.length
    || message.charts.length
    || message.reportComponent
    || message.dataSourceSuggestion
  );
}


export function settleCancelledAssistantMessage(
  messages: ChatMessage[],
  assistantMessageId: string,
): ChatMessage[] {
  const target = messages.find(message => message.id === assistantMessageId);
  if (!target) return messages;
  if (!hasAssistantContent(target)) {
    return messages.filter(message => message.id !== assistantMessageId);
  }
  return messages.map(message =>
    message.id === assistantMessageId
      ? {
          ...message,
          streaming: false,
          progressMessage: undefined,
          progressRequestId: undefined,
        }
      : message
  );
}
