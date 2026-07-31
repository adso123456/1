import type { AssistantAppearance } from './assistantAppearance';

export type WidgetRpcOperation =
  | 'application'
  | 'data-sources'
  | 'chat'
  | 'report-options'
  | 'report-generate'
  | 'report-preview'
  | 'report-pdf';

export type WidgetParentMessageType =
  | 'water-agent-widget:opened'
  | 'water-agent-widget:rpc-response'
  | 'water-agent-widget:rpc-chunk'
  | 'water-agent-widget:rpc-end'
  | 'water-agent-widget:rpc-error';

export type WidgetFrameMessageType =
  | 'water-agent-widget:ready'
  | 'water-agent-widget:close'
  | 'water-agent-widget:minimize'
  | 'water-agent-widget:appearance'
  | 'water-agent-widget:rpc-request'
  | 'water-agent-widget:rpc-cancel';

export interface WidgetEmbedContext {
  parentOrigin: string;
  instanceId: string;
  appId: string;
}

export interface WidgetMessage {
  type: WidgetParentMessageType | WidgetFrameMessageType;
  instanceId: string;
  requestId?: string;
}

export interface WidgetRpcRequestMessage extends WidgetMessage {
  type: 'water-agent-widget:rpc-request';
  requestId: string;
  operation: WidgetRpcOperation;
  payload?: unknown;
}

export type WidgetLoaderAppearance = Pick<
  AssistantAppearance,
  | 'theme'
  | 'float_icon_url'
  | 'float_icon_draggable'
  | 'float_x_anchor'
  | 'float_x_offset'
  | 'float_y_anchor'
  | 'float_y_offset'
>;

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
  const appId = url.searchParams.get('appId')?.trim() || '';
  if (
    !parentOrigin
    || !/^[A-Za-z0-9_-]{1,128}$/.test(instanceId)
    || !/^[A-Za-z0-9_-]{3,64}$/.test(appId)
  ) {
    return null;
  }
  return { parentOrigin, instanceId, appId };
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

export function isWidgetRpcMessage(
  event: MessageEvent,
  context: WidgetEmbedContext,
  expectedSource: Window,
): event is MessageEvent<WidgetMessage & { requestId: string }> {
  const data = event.data as Partial<WidgetMessage> | null;
  return (
    event.source === expectedSource
    && event.origin === context.parentOrigin
    && data?.instanceId === context.instanceId
    && typeof data.requestId === 'string'
    && /^[A-Za-z0-9_-]{1,128}$/.test(data.requestId)
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

export function postWidgetAppearanceMessage(
  target: Window,
  context: WidgetEmbedContext,
  appearance: WidgetLoaderAppearance,
): void {
  target.postMessage(
    {
      type: 'water-agent-widget:appearance',
      instanceId: context.instanceId,
      appearance: {
        theme: appearance.theme,
        float_icon_url: appearance.float_icon_url,
        float_icon_draggable: appearance.float_icon_draggable,
        float_x_anchor: appearance.float_x_anchor,
        float_x_offset: appearance.float_x_offset,
        float_y_anchor: appearance.float_y_anchor,
        float_y_offset: appearance.float_y_offset,
      },
    },
    context.parentOrigin,
  );
}
