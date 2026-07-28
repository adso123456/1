import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
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
  postWidgetAppearanceMessage,
  postWidgetMessage,
  readWidgetAuthMessage,
  readWidgetEmbedContext,
  type WidgetEmbedContext,
} from './widgetMessageProtocol';
import {
  DEFAULT_ASSISTANT_APPEARANCE,
  normalizeAssistantAppearance,
  type AssistantAppearance,
} from './assistantAppearance';

type DashboardTarget =
  | { mode: 'existing'; dashboardId: string }
  | { mode: 'new'; name: string };

type DashboardActions = ReturnType<typeof useDashboard>;

export interface WidgetApplicationConfig extends AssistantAppearance {
  app_id: string;
  name: string;
  show_history: boolean;
}

type WidgetApplicationConfigInput = Pick<
  WidgetApplicationConfig,
  'app_id' | 'name' | 'show_history'
> & Partial<AssistantAppearance>;

export const DEFAULT_WIDGET_APPLICATION_CONFIG: WidgetApplicationConfig = {
  ...DEFAULT_ASSISTANT_APPEARANCE,
  app_id: '',
  name: '智能问数',
  show_history: true,
};

export function normalizeWidgetApplicationConfig(
  value: unknown,
): WidgetApplicationConfig {
  if (typeof value !== 'object' || value === null) {
    return DEFAULT_WIDGET_APPLICATION_CONFIG;
  }
  const candidate = value as Partial<WidgetApplicationConfig>;
  const appearance = normalizeAssistantAppearance(candidate);
  return {
    ...appearance,
    app_id: typeof candidate.app_id === 'string' ? candidate.app_id : '',
    name: typeof candidate.name === 'string' && candidate.name.trim()
      ? candidate.name
      : DEFAULT_WIDGET_APPLICATION_CONFIG.name,
    show_history: candidate.show_history === true,
  };
}

interface WidgetChatProps {
  embedContext: WidgetEmbedContext;
  requestOptions?: UseSSERequestOptions;
  dashboard?: DashboardActions;
  workspaceEnabled: boolean;
  applicationConfig?: WidgetApplicationConfigInput;
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
  applicationConfig,
}: {
  onNewSession?: () => void;
  newSessionDisabled?: boolean;
  workspaceUrl?: string;
  embedContext: WidgetEmbedContext | null;
  applicationConfig: WidgetApplicationConfig;
}) {
  const [logoFailed, setLogoFailed] = useState(false);
  useEffect(() => setLogoFailed(false), [applicationConfig.logo_url]);
  return (
    <header className="widget-header">
      <div className="widget-title-block">
        {applicationConfig.logo_url && !logoFailed ? (
          <img
            className="widget-app-logo"
            src={applicationConfig.logo_url}
            alt=""
            onError={() => setLogoFailed(true)}
          />
        ) : (
          <span
            className="widget-status-dot"
            aria-hidden="true"
            style={{ backgroundColor: applicationConfig.theme }}
          />
        )}
        <div>
          <strong>{applicationConfig.name}</strong>
          <span>{applicationConfig.welcome}</span>
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
      <WidgetHeader
        embedContext={embedContext}
        applicationConfig={DEFAULT_WIDGET_APPLICATION_CONFIG}
      />
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
  applicationConfig: applicationConfigInput =
    DEFAULT_WIDGET_APPLICATION_CONFIG,
}: WidgetChatProps) {
  const applicationConfig = normalizeWidgetApplicationConfig(
    applicationConfigInput,
  );
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
    <div
      className="widget-shell"
      style={{
        '--widget-theme': applicationConfig.theme,
        '--widget-header-color': applicationConfig.header_font_color,
      } as CSSProperties}
    >
      <WidgetHeader
        embedContext={embedContext}
        onNewSession={createNewSession}
        newSessionDisabled={loading}
        workspaceUrl={workspaceUrl}
        applicationConfig={applicationConfig}
      />

      <div className="widget-session-bar">
        {applicationConfig.show_history && (
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
        )}

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
          welcome={applicationConfig.welcome}
          welcomeDescription={applicationConfig.welcome_description}
          theme={applicationConfig.theme}
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
    <ProtectedWidgetChat
      key={authGeneration}
      embedContext={embedContext}
      token={embedAuth.token}
      requestOptions={requestOptions}
      onAuthorizationError={handleAuthorizationError}
    />
  );
}

function ProtectedWidgetChat({
  embedContext,
  token,
  requestOptions,
  onAuthorizationError,
}: {
  embedContext: WidgetEmbedContext;
  token: string;
  requestOptions: UseSSERequestOptions;
  onAuthorizationError: () => void;
}) {
  const [applicationConfig, setApplicationConfig] =
    useState(DEFAULT_WIDGET_APPLICATION_CONFIG);
  const { parentOrigin, instanceId } = embedContext;

  useEffect(() => {
    const controller = new AbortController();

    void fetch('/api/embed/application', {
      headers: {
        Authorization: `Bearer ${token}`,
        'X-Water-Agent-Parent-Origin': parentOrigin,
      },
      signal: controller.signal,
    })
      .then(async response => {
        if (response.status === 401 || response.status === 403) {
          onAuthorizationError();
          return null;
        }
        if (!response.ok) {
          throw new Error(`应用配置请求失败：${response.status}`);
        }
        return response.json() as Promise<unknown>;
      })
      .then(value => {
        if (value !== null && !controller.signal.aborted) {
          const normalized = normalizeWidgetApplicationConfig(value);
          setApplicationConfig(normalized);
          postWidgetAppearanceMessage(
            window.parent,
            { parentOrigin, instanceId },
            normalized,
          );
        }
      })
      .catch(error => {
        if (
          !controller.signal.aborted
          && !(error instanceof DOMException && error.name === 'AbortError')
        ) {
          setApplicationConfig(DEFAULT_WIDGET_APPLICATION_CONFIG);
        }
      });

    return () => controller.abort();
  }, [
    instanceId,
    onAuthorizationError,
    parentOrigin,
    token,
  ]);

  return (
    <WidgetChat
      embedContext={embedContext}
      requestOptions={requestOptions}
      workspaceEnabled={false}
      applicationConfig={applicationConfig}
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
