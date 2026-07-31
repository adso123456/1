import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from 'react';
import { AddToDashboardDialog } from './components/AddToDashboardDialog';
import { ChatArea } from './components/ChatArea';
import {
  ReportPreviewModal,
  type ReportOptions,
  type ReportResultData,
} from './components/ReportComponents';
import { configFromReportResult } from './reportConfigState';
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
  readWidgetEmbedContext,
  type WidgetEmbedContext,
} from './widgetMessageProtocol';
import { WidgetRpcClient } from './widgetRpcClient';
import type { ReportRequest } from './components/ReportComponents';
import {
  DEFAULT_ASSISTANT_APPEARANCE,
  normalizeAssistantAppearance,
  type AssistantAppearance,
} from './assistantAppearance';
import {
  formatDatabaseType,
  formatDataSourceStatus,
} from './dataSourcePresentation';

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
  reportRequest?: ReportRequest;
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
      ? '嵌入服务加载失败，请联系系统管理员。'
      : '正在加载嵌入服务';
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
  reportRequest,
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
    replaceMessageReport,
    pendingReportConfig,
    updatePendingReportConfig,
    dismissPendingReportConfig,
    appendReportResult,
    sessionList,
    currentSessionId,
    createNewSession,
    switchToSession,
    storageError,
    dataSources,
    currentSourceId,
    dataSourceError,
    sourceBound,
  } = useSSE(undefined, requestOptions);
  const [pendingAdd, setPendingAdd] =
    useState<WidgetDashboardPayload | null>(null);
  const [notice, setNotice] =
    useState<{ ok: boolean; message: string } | null>(null);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [reportPreview, setReportPreview] =
    useState<ReportResultData | null>(null);

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
  const currentSource = dataSources.find(
    source => source.source_id === currentSourceId,
  );

  useEffect(() => {
    if (!pendingQuestion || !sourceBound || !currentSourceId || messages.length > 0) return;
    const question = pendingQuestion;
    setPendingQuestion(null);
    void sendMessage(question);
  }, [currentSourceId, messages.length, pendingQuestion, sendMessage, sourceBound]);

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

  const handleReportReconfigure = useCallback(async (
    result: ReportResultData,
  ) => {
    try {
      const response = reportRequest
        ? await reportRequest('report-options')
        : await fetch('/api/reports/water-quality/options', {
            headers: requestOptions?.headersProvider?.(),
          });
      if (!response.ok) throw new Error();
      const options = await response.json() as ReportOptions;
      updatePendingReportConfig(configFromReportResult(result, options));
    } catch {
      setNotice({ ok: false, message: '最新报表筛选项加载失败，请稍后重试。' });
    }
  }, [reportRequest, requestOptions, updatePendingReportConfig]);

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
              onChange={event => {
                if (event.target.value) {
                  void createNewSession(event.target.value);
                }
              }}
            >
              <option value="">请选择</option>
              {dataSources.map(source => (
                <option key={source.source_id} value={source.source_id}>
                  {source.display_name || formatDatabaseType(source.database_type)}
                </option>
              ))}
            </select>
          ) : (
            <span className="widget-source-badge">
              {currentSource?.display_name
                || (currentSource
                  ? formatDatabaseType(currentSource.database_type)
                  : '加载中')}
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
          onReportGenerated={(messageId, result) => {
            replaceMessageReport(
              messageId,
              result as unknown as Record<string, unknown>,
            );
            setReportPreview(result);
          }}
          onReportPreview={setReportPreview}
          onReportReconfigure={handleReportReconfigure}
          pendingReportConfig={pendingReportConfig}
          onReportConfigChange={updatePendingReportConfig}
          onReportConfigCancel={dismissPendingReportConfig}
          onReportConfigGenerated={result => {
            appendReportResult(result);
            setReportPreview(result);
          }}
          reportRequestHeaders={requestOptions?.headersProvider}
          reportRequest={reportRequest}
          onAddToDashboard={
            dashboard ? handleRequestAddToDashboard : undefined
          }
          compact
          workspaceUrl={workspaceUrl}
          hideHeader
          welcome={applicationConfig.welcome}
          welcomeDescription={applicationConfig.welcome_description}
          theme={applicationConfig.theme}
          sourceLabel={currentSource
            ? `${currentSource.display_name || formatDatabaseType(currentSource.database_type)} · ${formatDatabaseType(currentSource.database_type)} · ${formatDataSourceStatus(currentSource.status, currentSource.enabled_for_chat)}`
            : ''}
          dataSources={dataSources}
          onDataSourceSuggestion={async (sourceId, question) => {
            const ok = await createNewSession(sourceId);
            if (ok) setPendingQuestion(question);
            return ok;
          }}
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

      <ReportPreviewModal
        result={reportPreview}
        onClose={() => setReportPreview(null)}
        reportRequest={reportRequest}
      />
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

function BridgedWidgetChat({
  embedContext,
}: {
  embedContext: WidgetEmbedContext;
}) {
  const [applicationConfig, setApplicationConfig] =
    useState(DEFAULT_WIDGET_APPLICATION_CONFIG);
  const [loadError, setLoadError] = useState(false);
  const rpcClient = useMemo(
    () => new WidgetRpcClient(embedContext),
    [embedContext],
  );
  const requestOptions = useMemo<UseSSERequestOptions>(() => ({
    enabled: true,
    dataSourcesEndpoint: 'widget-rpc:data-sources',
    chatEndpoint: 'widget-rpc:chat',
    persistenceMode: 'memory',
    bindConversationOnCreate: false,
    fetcher: (url, init) => {
      if (url === 'widget-rpc:data-sources') {
        return rpcClient.request(
          'data-sources',
          undefined,
          init?.signal ?? undefined,
        );
      }
      if (url === 'widget-rpc:chat') {
        const payload = typeof init?.body === 'string'
          ? JSON.parse(init.body)
          : init?.body;
        return rpcClient.request('chat', payload, init?.signal ?? undefined);
      }
      throw new Error(`Widget RPC 不支持请求：${url}`);
    },
  }), [rpcClient]);
  const reportRequest = useCallback<ReportRequest>(
    (operation, payload, signal) =>
      rpcClient.request(operation, payload, signal),
    [rpcClient],
  );

  useEffect(() => {
    const controller = new AbortController();
    rpcClient.connect();
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
    void rpcClient.request('application', undefined, controller.signal)
      .then(async response => {
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
            embedContext,
            normalized,
          );
          setLoadError(false);
        }
      })
      .catch(error => {
        if (
          !controller.signal.aborted
          && !(error instanceof DOMException && error.name === 'AbortError')
        ) {
          setApplicationConfig(DEFAULT_WIDGET_APPLICATION_CONFIG);
          setLoadError(true);
        }
      });

    return () => {
      controller.abort();
      window.removeEventListener('message', handleWidgetMessage);
      rpcClient.destroy();
    };
  }, [embedContext, rpcClient]);

  if (loadError) {
    return <WidgetAccessView embedContext={embedContext} status="error" />;
  }

  return (
    <WidgetChat
      embedContext={embedContext}
      requestOptions={requestOptions}
      workspaceEnabled={false}
      applicationConfig={applicationConfig}
      reportRequest={reportRequest}
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
  return <BridgedWidgetChat embedContext={embedContext} />;
}
