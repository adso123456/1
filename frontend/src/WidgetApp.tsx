import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AddToDashboardDialog } from './components/AddToDashboardDialog';
import { ChatArea } from './components/ChatArea';
import {
  buildWorkspaceUrl,
  resolveWidgetAccessMode,
} from './appMode';
import { useDashboard } from './hooks/useDashboard';
import {
  useSSE,
  type UseSSERequestOptions,
} from './hooks/useSSE';
import {
  createWidgetDashboardChartItem,
  type WidgetDashboardPayload,
} from './widgetDashboardSnapshot';
import {
  isWidgetMessage,
  postWidgetMessage,
  readWidgetAuthMessage,
  readWidgetEmbedContext,
  type WidgetEmbedContext,
} from './widgetMessageProtocol';

type DashboardTarget =
  | { mode: 'existing'; dashboardId: string }
  | { mode: 'new'; name: string };

type DashboardActions = ReturnType<typeof useDashboard>;

interface WidgetChatProps {
  embedContext: WidgetEmbedContext;
  requestOptions?: UseSSERequestOptions;
  dashboard?: DashboardActions;
  workspaceEnabled: boolean;
}

function requestWidgetMinimize(context: WidgetEmbedContext | null): void {
  if (!context) return;
  postWidgetMessage(
    window.parent,
    context,
    'water-agent-widget:minimize',
  );
}

function WidgetHeader({
  onNewSession,
  newSessionDisabled,
  workspaceUrl,
  embedContext,
}: {
  onNewSession?: () => void;
  newSessionDisabled?: boolean;
  workspaceUrl?: string;
  embedContext: WidgetEmbedContext | null;
}) {
  return (
    <header className="widget-header">
      <div className="widget-title-block">
        <span className="widget-status-dot" aria-hidden="true" />
        <div>
          <strong>智能问数</strong>
          <span>水利数据助手</span>
        </div>
      </div>
      <div className="widget-header-actions">
        {onNewSession && (
          <button
            type="button"
            onClick={onNewSession}
            disabled={newSessionDisabled}
            title="新建会话"
          >
            新建
          </button>
        )}
        {workspaceUrl && (
          <a
            href={workspaceUrl}
            target="_blank"
            rel="noreferrer"
            title="在新标签页打开完整工作台"
          >
            完整工作台
          </a>
        )}
        {embedContext && (
          <button
            type="button"
            className="widget-icon-button"
            onClick={() => requestWidgetMinimize(embedContext)}
            title="最小化"
            aria-label="最小化智能问数"
          >
            —
          </button>
        )}
      </div>
    </header>
  );
}

export function WidgetAccessView({
  embedContext,
  status,
}: {
  embedContext: WidgetEmbedContext | null;
  status: 'invalid' | 'waiting' | 'error';
}) {
  const message = status === 'invalid'
    ? '无效的嵌入访问入口'
    : status === 'error'
      ? '嵌入访问验证失败，请联系系统管理员。'
      : '正在验证嵌入访问权限';
  return (
    <div className="widget-shell">
      <WidgetHeader embedContext={embedContext} />
      <div
        className="widget-error"
        role={status === 'waiting' ? 'status' : 'alert'}
      >
        {message}
      </div>
    </div>
  );
}

export function WidgetChat({
  embedContext,
  requestOptions,
  dashboard,
  workspaceEnabled,
}: WidgetChatProps) {
  const {
    messages,
    loading,
    sendMessage,
    cancelRequest,
    clearMessages,
    replaceMessageChart,
    sessionList,
    currentSessionId,
    createNewSession,
    switchToSession,
    storageError,
    dataSources,
    currentSourceId,
    selectDataSource,
    dataSourceError,
    sourceBound,
  } = useSSE(undefined, requestOptions);
  const [pendingAdd, setPendingAdd] =
    useState<WidgetDashboardPayload | null>(null);
  const [notice, setNotice] =
    useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!notice?.ok) return;
    const timer = window.setTimeout(() => setNotice(null), 2500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const currentSessionExists = sessionList.some(
    session => session.id === currentSessionId,
  );
  const workspaceUrl = workspaceEnabled
    ? buildWorkspaceUrl(window.location.origin, currentSessionId)
    : undefined;

  const handleRequestAddToDashboard = useCallback(
    (payload: WidgetDashboardPayload) => {
      if (!dashboard) return;
      setNotice(null);
      setPendingAdd(payload);
    },
    [dashboard],
  );

  const handleConfirmAddToDashboard = useCallback(
    (target: DashboardTarget) => {
      if (!pendingAdd || !dashboard) return;
      const item = createWidgetDashboardChartItem(
        pendingAdd,
        currentSessionId,
      );
      const targetId = target.mode === 'existing'
        ? (
            dashboard.addItemsToDashboard(target.dashboardId, [item])
              ? target.dashboardId
              : null
          )
        : dashboard.createDashboardWithItems(target.name, [item]);

      setPendingAdd(null);
      setNotice(targetId
        ? { ok: true, message: '已添加到仪表板' }
        : {
            ok: false,
            message: '添加失败，localStorage 可能已满或仪表板不存在，请重试。',
          });
    },
    [currentSessionId, dashboard, pendingAdd],
  );

  return (
    <div className="widget-shell">
      <WidgetHeader
        embedContext={embedContext}
        onNewSession={createNewSession}
        newSessionDisabled={loading}
        workspaceUrl={workspaceUrl}
      />

      <div className="widget-session-bar">
        <label>
          <span>会话</span>
          <select
            aria-label="选择会话"
            value={currentSessionId}
            disabled={loading}
            onChange={event => switchToSession(event.target.value)}
          >
            {!currentSessionExists && (
              <option value={currentSessionId}>当前新会话</option>
            )}
            {sessionList.map(session => (
              <option key={session.id} value={session.id}>
                {session.title}
              </option>
            ))}
          </select>
        </label>

        <label>
          <span>数据源</span>
          {dataSources.length > 1 ? (
            <select
              aria-label="选择数据源"
              value={currentSourceId}
              disabled={loading || sourceBound}
              onChange={event => selectDataSource(event.target.value)}
            >
              <option value="">请选择</option>
              {dataSources.map(source => (
                <option key={source.source_id} value={source.source_id}>
                  {source.source_id}
                </option>
              ))}
            </select>
          ) : (
            <span className="widget-source-badge">
              {currentSourceId || '加载中'}
            </span>
          )}
        </label>
      </div>

      {(dataSourceError || storageError) && (
        <div className="widget-error" role="alert">
          {dataSourceError || storageError}
        </div>
      )}

      <div className="widget-chat">
        <ChatArea
          messages={messages}
          loading={loading}
          onSend={sendMessage}
          onCancel={cancelRequest}
          onClear={clearMessages}
          onChangeChartType={() => {}}
          onV2ChartSwitch={replaceMessageChart}
          onAddToDashboard={
            dashboard ? handleRequestAddToDashboard : undefined
          }
          compact
          workspaceUrl={workspaceUrl}
          hideHeader
        />
      </div>

      {pendingAdd && dashboard && (
        <AddToDashboardDialog
          dashboards={dashboard.dashboards}
          currentDashboardId={dashboard.currentDashboardId}
          onConfirm={handleConfirmAddToDashboard}
          onClose={() => setPendingAdd(null)}
        />
      )}

      {notice && (
        <div
          className={`widget-toast ${notice.ok ? 'widget-toast--success' : 'widget-toast--error'}`}
          role={notice.ok ? 'status' : 'alert'}
        >
          <span>{notice.message}</span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            aria-label="关闭提示"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}

function DevelopmentWidgetChat({
  embedContext,
}: {
  embedContext: WidgetEmbedContext;
}) {
  const dashboard = useDashboard();

  useEffect(() => {
    const handleWidgetMessage = (event: MessageEvent) => {
      if (
        isWidgetMessage(
          event,
          embedContext,
          'water-agent-widget:opened',
          window.parent,
        )
      ) {
        window.dispatchEvent(new Event('water-agent-widget:opened'));
      }
    };
    window.addEventListener('message', handleWidgetMessage);
    postWidgetMessage(window.parent, embedContext, 'water-agent-widget:ready');
    return () => {
      window.removeEventListener('message', handleWidgetMessage);
    };
  }, [embedContext]);

  return (
    <WidgetChat
      embedContext={embedContext}
      dashboard={dashboard}
      workspaceEnabled
    />
  );
}

function ProtectedWidgetGate({
  embedContext,
}: {
  embedContext: WidgetEmbedContext;
}) {
  const [embedAuth, setEmbedAuth] = useState<{
    status: 'waiting' | 'authorized' | 'error';
    token: string;
    expiresAt: number;
  }>({
    status: 'waiting',
    token: '',
    expiresAt: 0,
  });
  const [authGeneration, setAuthGeneration] = useState(0);
  const authRequiredSentRef = useRef(false);

  const handleAuthorizationError = useCallback(() => {
    setEmbedAuth({ status: 'error', token: '', expiresAt: 0 });
    if (authRequiredSentRef.current) return;
    authRequiredSentRef.current = true;
    postWidgetMessage(
      window.parent,
      embedContext,
      'water-agent-widget:auth-required',
    );
  }, [embedContext]);

  useEffect(() => {
    const handleWidgetMessage = (event: MessageEvent) => {
      const auth = readWidgetAuthMessage(
        event,
        embedContext,
        window.parent,
      );
      if (auth) {
        if (auth.expiresAt <= Math.floor(Date.now() / 1000)) {
          handleAuthorizationError();
        } else {
          authRequiredSentRef.current = false;
          setAuthGeneration(current => current + 1);
          setEmbedAuth({
            status: 'authorized',
            token: auth.token,
            expiresAt: auth.expiresAt,
          });
        }
        return;
      }
      if (
        isWidgetMessage(
          event,
          embedContext,
          'water-agent-widget:opened',
          window.parent,
        )
      ) {
        window.dispatchEvent(new Event('water-agent-widget:opened'));
      }
    };
    window.addEventListener('message', handleWidgetMessage);
    postWidgetMessage(window.parent, embedContext, 'water-agent-widget:ready');
    return () => {
      window.removeEventListener('message', handleWidgetMessage);
    };
  }, [embedContext, handleAuthorizationError]);

  useEffect(() => {
    if (embedAuth.status !== 'authorized') return;
    const delay = Math.max(0, embedAuth.expiresAt * 1000 - Date.now());
    const timer = window.setTimeout(handleAuthorizationError, delay);
    return () => window.clearTimeout(timer);
  }, [
    embedAuth.expiresAt,
    embedAuth.status,
    handleAuthorizationError,
  ]);

  const requestOptions = useMemo<UseSSERequestOptions>(() => ({
    enabled: true,
    dataSourcesEndpoint: '/api/embed/data-sources',
    chatEndpoint: '/api/embed/vanna/v2/chat_sse',
    headersProvider: () => ({
      Authorization: `Bearer ${embedAuth.token}`,
      'X-Water-Agent-Parent-Origin': embedContext.parentOrigin,
    }),
    onAuthorizationError: handleAuthorizationError,
    persistenceMode: 'memory',
  }), [
    embedAuth.token,
    embedContext.parentOrigin,
    handleAuthorizationError,
  ]);

  if (embedAuth.status !== 'authorized') {
    return (
      <WidgetAccessView
        embedContext={embedContext}
        status={embedAuth.status}
      />
    );
  }

  return (
    <WidgetChat
      key={authGeneration}
      embedContext={embedContext}
      requestOptions={requestOptions}
      workspaceEnabled={false}
    />
  );
}

export function WidgetApp() {
  const embedContext = useMemo(
    () => readWidgetEmbedContext(window.location.href),
    [],
  );
  const widgetAccessMode = useMemo(
    () => resolveWidgetAccessMode(
      window.location.href,
      window.location.origin,
      import.meta.env.DEV,
    ),
    [],
  );

  if (widgetAccessMode === 'invalid' || !embedContext) {
    return (
      <WidgetAccessView
        embedContext={embedContext}
        status="invalid"
      />
    );
  }
  if (widgetAccessMode === 'local-development') {
    return <DevelopmentWidgetChat embedContext={embedContext} />;
  }
  return <ProtectedWidgetGate embedContext={embedContext} />;
}
