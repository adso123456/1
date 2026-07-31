import {
  isWidgetRpcMessage,
  type WidgetEmbedContext,
  type WidgetRpcOperation,
} from './widgetMessageProtocol';

interface PendingRequest {
  resolve: (response: Response) => void;
  reject: (reason: unknown) => void;
  stream: boolean;
  streamController: ReadableStreamDefaultController<Uint8Array> | null;
  abortCleanup: () => void;
}

interface RpcResponseMessage {
  type: 'water-agent-widget:rpc-response';
  requestId: string;
  status: number;
  body?: unknown;
  contentType?: string;
}

interface RpcChunkMessage {
  type: 'water-agent-widget:rpc-chunk';
  requestId: string;
  chunk: string;
}

interface RpcErrorMessage {
  type: 'water-agent-widget:rpc-error';
  requestId: string;
  status: number;
  message: string;
}

function requestId(): string {
  return `rpc-${crypto.randomUUID()}`;
}

function responseBody(value: unknown, contentType: string): BodyInit | null {
  if (value === undefined || value === null) return null;
  if (typeof value === 'string') return value;
  if (contentType.includes('application/json')) return JSON.stringify(value);
  return String(value);
}

export class WidgetRpcClient {
  private readonly pending = new Map<string, PendingRequest>();
  private readonly context: WidgetEmbedContext;
  private connected = false;
  private readonly handleMessage = (event: MessageEvent) => {
    if (!isWidgetRpcMessage(event, this.context, window.parent)) return;
    const message = event.data as (
      RpcResponseMessage | RpcChunkMessage | RpcErrorMessage
      | { type: 'water-agent-widget:rpc-end'; requestId: string }
    );
    const pending = this.pending.get(message.requestId);
    if (!pending) return;
    if (message.type === 'water-agent-widget:rpc-chunk') {
      pending.streamController?.enqueue(
        new TextEncoder().encode(message.chunk),
      );
      return;
    }
    if (message.type === 'water-agent-widget:rpc-end') {
      pending.streamController?.close();
      this.finish(message.requestId);
      return;
    }
    if (message.type === 'water-agent-widget:rpc-error') {
      const body = JSON.stringify({ detail: message.message });
      if (pending.streamController) {
        pending.streamController.error(new Error(message.message));
        this.finish(message.requestId);
      } else {
        pending.resolve(new Response(body, {
          status: message.status,
          headers: { 'Content-Type': 'application/json' },
        }));
        this.finish(message.requestId);
      }
      return;
    }
    const contentType = message.contentType || 'application/json';
    if (pending.stream) {
      if (message.status >= 400) {
        pending.resolve(new Response(
          responseBody(message.body, contentType),
          {
            status: message.status,
            headers: { 'Content-Type': contentType },
          },
        ));
        this.finish(message.requestId);
        return;
      }
      let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
      const stream = new ReadableStream<Uint8Array>({
        start(value) {
          controller = value;
        },
        cancel: () => {
          this.cancel(message.requestId);
        },
      });
      pending.streamController = controller;
      pending.resolve(new Response(stream, {
        status: message.status,
        headers: { 'Content-Type': contentType },
      }));
      return;
    }
    pending.resolve(new Response(responseBody(message.body, contentType), {
      status: message.status,
      headers: { 'Content-Type': contentType },
    }));
    this.finish(message.requestId);
  };

  constructor(context: WidgetEmbedContext) {
    this.context = context;
    this.connect();
  }

  connect(): void {
    if (this.connected) return;
    window.addEventListener('message', this.handleMessage);
    this.connected = true;
  }

  request(
    operation: WidgetRpcOperation,
    payload?: unknown,
    signal?: AbortSignal,
  ): Promise<Response> {
    const id = requestId();
    const stream = operation === 'chat';
    return new Promise<Response>((resolve, reject) => {
      const cancel = () => {
        this.cancel(id);
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      };
      if (signal?.aborted) {
        cancel();
        return;
      }
      signal?.addEventListener('abort', cancel, { once: true });
      this.pending.set(id, {
        resolve,
        reject,
        stream,
        streamController: null,
        abortCleanup: () => signal?.removeEventListener('abort', cancel),
      });
      window.parent.postMessage(
        {
          type: 'water-agent-widget:rpc-request',
          instanceId: this.context.instanceId,
          requestId: id,
          operation,
          payload,
        },
        this.context.parentOrigin,
      );
    });
  }

  destroy(): void {
    if (this.connected) {
      window.removeEventListener('message', this.handleMessage);
      this.connected = false;
    }
    for (const [id, pending] of this.pending) {
      pending.reject(new Error('Widget RPC client destroyed'));
      pending.abortCleanup();
      this.pending.delete(id);
    }
  }

  private cancel(id: string): void {
    if (!this.pending.has(id)) return;
    window.parent.postMessage(
      {
        type: 'water-agent-widget:rpc-cancel',
        instanceId: this.context.instanceId,
        requestId: id,
      },
      this.context.parentOrigin,
    );
    const pending = this.pending.get(id);
    pending?.streamController?.error(
      new DOMException('The operation was aborted.', 'AbortError'),
    );
    this.finish(id);
  }

  private finish(id: string): void {
    const pending = this.pending.get(id);
    pending?.abortCleanup();
    this.pending.delete(id);
  }
}
