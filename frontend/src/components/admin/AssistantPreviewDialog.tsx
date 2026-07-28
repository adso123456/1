import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { buildAdminPreviewWidgetUrl } from '../../appMode';
import type { AdminPreviewTokenResponse } from '../../adminTypes';
import {
  isWidgetMessage,
  postWidgetAuthMessage,
  type WidgetEmbedContext,
} from '../../widgetMessageProtocol';

interface AssistantPreviewDialogProps {
  appId: string;
  appName: string;
  issueToken: (
    appId: string,
    signal: AbortSignal,
  ) => Promise<AdminPreviewTokenResponse>;
  onClose: () => void;
}

interface PreviewSession {
  id: number;
  instanceId: string;
  context: WidgetEmbedContext;
  url: string;
}

function createInstanceId(): string {
  return `preview-${crypto.randomUUID()}`;
}

function createSession(id: number): PreviewSession {
  const instanceId = createInstanceId();
  const parentOrigin = window.location.origin;
  return {
    id,
    instanceId,
    context: { parentOrigin, instanceId },
    url: buildAdminPreviewWidgetUrl(
      parentOrigin,
      parentOrigin,
      instanceId,
    ),
  };
}

export function AssistantPreviewDialog({
  appId,
  appName,
  issueToken,
  onClose,
}: AssistantPreviewDialogProps) {
  const nextSessionIdRef = useRef(1);
  const nextRequestIdRef = useRef(1);
  const sessionRef = useRef<PreviewSession | null>(null);
  if (sessionRef.current === null) {
    sessionRef.current = createSession(nextSessionIdRef.current++);
  }
  const requestRef = useRef<{
    sessionId: number;
    requestId: number;
    controller: AbortController;
  } | null>(null);
  const lifecycleGenerationRef = useRef(0);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [session, setSession] = useState(() => sessionRef.current!);
  const [listenerReady, setListenerReady] = useState(false);
  const [status, setStatus] = useState('等待 protected Widget 鉴权…');
  const [error, setError] = useState('');

  const invalidateSession = useCallback(() => {
    sessionRef.current = null;
    requestRef.current?.controller.abort();
    requestRef.current = null;
  }, []);

  const close = useCallback(() => {
    invalidateSession();
    onClose();
  }, [invalidateSession, onClose]);

  const reload = useCallback(() => {
    invalidateSession();
    const next = createSession(nextSessionIdRef.current++);
    sessionRef.current = next;
    setListenerReady(false);
    setSession(next);
    setError('');
    setStatus('等待 protected Widget 鉴权…');
  }, [invalidateSession]);

  useEffect(() => {
    lifecycleGenerationRef.current += 1;
    return () => {
      lifecycleGenerationRef.current += 1;
      const cleanupGeneration = lifecycleGenerationRef.current;
      window.queueMicrotask(() => {
        if (
          lifecycleGenerationRef.current === cleanupGeneration
        ) {
          invalidateSession();
        }
      });
    };
  }, [invalidateSession]);

  const requestAuthorization = useCallback((
    current: PreviewSession,
    frameWindow: Window,
  ) => {
    if (
      requestRef.current
      || sessionRef.current?.id !== current.id
      || iframeRef.current?.contentWindow !== frameWindow
    ) {
      return;
    }
    const requestId = nextRequestIdRef.current++;
    const controller = new AbortController();
    requestRef.current = {
      sessionId: current.id,
      requestId,
      controller,
    };
    setError('');
    setStatus('正在取得短期预览授权…');
    void issueToken(appId, controller.signal)
      .then(response => {
        const owner = requestRef.current;
        const active = sessionRef.current;
        const target = iframeRef.current?.contentWindow;
        if (
          !owner
          || owner.requestId !== requestId
          || owner.sessionId !== current.id
          || active?.id !== current.id
          || target !== frameWindow
        ) {
          return;
        }
        postWidgetAuthMessage(
          target,
          current.context,
          response.token,
          response.expires_at,
        );
        setStatus('预览已安全连接。');
      })
      .catch(reason => {
        const owner = requestRef.current;
        if (
          owner?.requestId !== requestId
          || sessionRef.current?.id !== current.id
          || (reason instanceof DOMException
            && reason.name === 'AbortError')
        ) {
          return;
        }
        setStatus('');
        setError(
          reason instanceof Error
            ? reason.message
            : '无法取得预览授权。',
        );
      })
      .finally(() => {
        const owner = requestRef.current;
        if (
          owner?.requestId === requestId
          && owner.sessionId === current.id
        ) {
          requestRef.current = null;
        }
      });
  }, [appId, issueToken]);

  useLayoutEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      const current = sessionRef.current;
      const frameWindow = iframeRef.current?.contentWindow;
      if (!current || !frameWindow) return;
      const isReady = isWidgetMessage(
        event,
        current.context,
        'water-agent-widget:ready',
        frameWindow,
      );
      const isAuthRequired = isWidgetMessage(
        event,
        current.context,
        'water-agent-widget:auth-required',
        frameWindow,
      );
      if (
        isWidgetMessage(
          event,
          current.context,
          'water-agent-widget:close',
          frameWindow,
        )
        || isWidgetMessage(
          event,
          current.context,
          'water-agent-widget:minimize',
          frameWindow,
        )
      ) {
        close();
        return;
      }
      if (!isReady && !isAuthRequired) return;
      requestAuthorization(current, frameWindow);
    };
    window.addEventListener('message', handleMessage);
    setListenerReady(true);
    return () => window.removeEventListener('message', handleMessage);
  }, [close, requestAuthorization, session]);

  return (
    <div className="admin-modal-backdrop" role="presentation">
      <section
        className="admin-modal admin-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-preview-title"
      >
        <div className="admin-modal-header">
          <div>
            <p className="admin-eyebrow">真实 protected Widget 预览</p>
            <h2 id="admin-preview-title">{appName}</h2>
            <code>{appId}</code>
          </div>
          <button className="admin-button" type="button" onClick={close}>
            关闭
          </button>
        </div>
        <p className="admin-preview-status" role="status">
          {error || status}
        </p>
        {listenerReady && (
          <iframe
            key={session.instanceId}
            ref={iframeRef}
            className="admin-preview-frame"
            title={`${appName} protected Widget 预览`}
            src={session.url}
            allow="clipboard-write"
          />
        )}
        <div className="admin-modal-actions">
          <button
            className="admin-button"
            type="button"
            onClick={reload}
          >
            重新加载
          </button>
          <button
            className="admin-button admin-button-primary"
            type="button"
            onClick={close}
          >
            关闭预览
          </button>
        </div>
      </section>
    </div>
  );
}
