export type WidgetParentMessageType =
  | 'water-agent-widget:opened'
  | 'water-agent-widget:auth';
export type WidgetFrameMessageType =
  | 'water-agent-widget:ready'
  | 'water-agent-widget:close'
  | 'water-agent-widget:minimize'
  | 'water-agent-widget:auth-required';

export interface WidgetEmbedContext {
  parentOrigin: string;
  instanceId: string;
}

interface WidgetMessage {
  type: WidgetParentMessageType | WidgetFrameMessageType;
  instanceId: string;
}

export interface WidgetAuthMessage {
  token: string;
  expiresAt: number;
}

function normalizeOrigin(value: string): string {
  try {
    const url = new URL(value);
    return url.origin === value ? value : '';
  } catch {
    return '';
  }
}

export function readWidgetEmbedContext(
  urlValue: string,
): WidgetEmbedContext | null {
  const url = new URL(urlValue);
  const parentOrigin = normalizeOrigin(
    url.searchParams.get('parentOrigin') || '',
  );
  const instanceId = url.searchParams.get('instanceId')?.trim() || '';
  if (!parentOrigin || !/^[A-Za-z0-9_-]{1,128}$/.test(instanceId)) {
    return null;
  }
  return { parentOrigin, instanceId };
}

export function isWidgetMessage(
  event: MessageEvent,
  context: WidgetEmbedContext,
  type: WidgetMessage['type'],
  expectedSource: Window,
): boolean {
  const data = event.data as Partial<WidgetMessage> | null;
  return (
    event.source === expectedSource
    && event.origin === context.parentOrigin
    && data?.type === type
    && data.instanceId === context.instanceId
  );
}

export function postWidgetMessage(
  target: Window,
  context: WidgetEmbedContext,
  type: WidgetFrameMessageType,
): void {
  target.postMessage(
    { type, instanceId: context.instanceId },
    context.parentOrigin,
  );
}

export function readWidgetAuthMessage(
  event: MessageEvent,
  context: WidgetEmbedContext,
  expectedSource: Window,
): WidgetAuthMessage | null {
  if (
    !isWidgetMessage(
      event,
      context,
      'water-agent-widget:auth',
      expectedSource,
    )
  ) {
    return null;
  }
  const data = event.data as Partial<WidgetAuthMessage>;
  if (
    typeof data.token !== 'string'
    || !data.token
    || typeof data.expiresAt !== 'number'
  ) {
    return null;
  }
  return { token: data.token, expiresAt: data.expiresAt };
}
