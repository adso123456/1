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
import type {
  SessionMeta,
  SuggestedQuestion,
  SuggestedQuestionsResponse,
} from './types';

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
            onClick={() => onNewSession()}
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

/**
 * 自定义会话下拉框：点击展开会话列表，每个会话项尾部带删除图标。
 * 原生 <select> 不支持项内按钮，故自绘下拉以支持删除任意会话。
 */
function WidgetSessionSelect({
  sessions,
  currentSessionId,
  currentSessionExists,
  disabled,
  onSwitch,
  onDelete,
}: {
  sessions: SessionMeta[];
  currentSessionId: string;
  currentSessionExists: boolean;
  disabled?: boolean;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // 点击下拉外部时收起
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  const currentTitle = currentSessionExists
    ? (sessions.find(session => session.id === currentSessionId)?.title || '当前新会话')
    : '当前新会话';

  return (
    <div className="widget-session-select" ref={rootRef}>
      <button
        type="button"
        className="widget-session-select__button"
        aria-label="选择会话"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen(prev => !prev)}
      >
        <span className="widget-session-select__title">{currentTitle}</span>
        <span className="widget-session-select__caret">▾</span>
      </button>
      {open && (
        <div className="widget-session-select__menu">
          {!currentSessionExists && (
            <div className="widget-session-select__item widget-session-select__item--current">
              <span className="widget-session-select__title">当前新会话</span>
            </div>
          )}
          {sessions.map(session => (
            <div
              key={session.id}
              className={`widget-session-select__item${session.id === currentSessionId ? ' widget-session-select__item--active' : ''}`}
              onClick={() => {
                setOpen(false);
                onSwitch(session.id);
              }}
            >
              <span className="widget-session-select__title">{session.title}</span>
              <button
                type="button"
                className="widget-session-select__delete"
                title={`删除会话「${session.title}」`}
                aria-label={`删除会话「${session.title}」`}
                onClick={event => {
                  event.stopPropagation();
                  if (window.confirm(`确定删除会话「${session.title}」吗？`)) {
                    onDelete(session.id);
                  }
                }}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
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
    deleteSession,
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
  const [suggestions, setSuggestions] = useState<SuggestedQuestion[]>([]);

  // 新会话（已绑定数据源且无消息）时，拉取数据源专属推荐问题；
  // 直连模式用默认 fetch，真实嵌入（Bridged）模式走 RPC fetcher
  useEffect(() => {
    if (!currentSessionId || !sourceBound || messages.length > 0) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    const fetcher = requestOptions?.fetcher;
    const request = fetcher
      ? fetcher(`widget-rpc:suggested-questions?conversation_id=${encodeURIComponent(currentSessionId)}`)
      : fetch(`/api/conversations/${encodeURIComponent(currentSessionId)}/suggested-questions`);
    request
      .then(async response => {
        if (response.status === 404 || !response.ok) return null;
        const payload = (await response.json()) as SuggestedQuestionsResponse;
        return Array.isArray(payload.questions) ? payload.questions : [];
      })
      .then(questions => {
        if (!cancelled && questions !== null) setSuggestions(questions);
      })
      .catch(() => {
        if (!cancelled) setSuggestions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [currentSessionId, messages.length, requestOptions?.fetcher, sourceBound]);

  useEffect(() => {
    if (!notice?.ok) return;
    const timer = window.setTimeout(() => setNotice(null), 2500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const currentSessionExists = sessionList.some(
    session => session.id === currentSessionId,
  );
  const workspaceUrl = workspaceEnabled
    ? buildWorkspaceUrl(
        window.location.origin,
        currentSessionId,
        requestOptions?.persistenceNamespace,
      )
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
          <WidgetSessionSelect
            sessions={sessionList}
            currentSessionId={currentSessionId}
            currentSessionExists={currentSessionExists}
            disabled={loading}
            onSwitch={switchToSession}
            onDelete={deleteSession}
          />
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

        <button
          type="button"
          className="widget-session-bar__clear"
          onClick={() => {
            if (window.confirm('确定清空当前会话的所有消息吗？')) {
              clearMessages();
            }
          }}
        >
          清空对话
        </button>
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
          suggestions={suggestions}
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
  const persistenceNamespace = `${embedContext.appId}:${embedContext.parentOrigin}`;
  const dashboard = useDashboard(persistenceNamespace);
  const requestOptions = useMemo<UseSSERequestOptions>(() => ({
    enabled: true,
    dataSourcesEndpoint: 'widget-rpc:data-sources',
    chatEndpoint: 'widget-rpc:chat',
    persistenceMode: 'local',
    persistenceNamespace,
    bindConversationOnCreate: true,
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
      const bindMatch = url.match(/^\/api\/conversations\/([^/]+)\/source$/);
      if (bindMatch) {
        const payload = typeof init?.body === 'string'
          ? JSON.parse(init.body)
          : init?.body as { source_id?: string } | undefined;
        return rpcClient.request(
          'bind-conversation-source',
          {
            conversationId: decodeURIComponent(bindMatch[1]),
            sourceId: payload?.source_id || '',
          },
          init?.signal ?? undefined,
        );
      }
      if (url.startsWith('widget-rpc:suggested-questions')) {
        const query = url.split('?')[1] || '';
        const conversationId = new URLSearchParams(query).get('conversation_id') || '';
        return rpcClient.request(
          'suggested-questions',
          { conversationId },
          init?.signal ?? undefined,
        );
      }
      throw new Error(`Widget RPC 不支持请求：${url}`);
    },
  }), [persistenceNamespace, rpcClient]);
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
      dashboard={dashboard}
      workspaceEnabled
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
