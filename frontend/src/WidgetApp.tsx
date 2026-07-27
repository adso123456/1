import { useCallback, useEffect, useMemo, useState } from 'react';
import { AddToDashboardDialog } from './components/AddToDashboardDialog';
import { ChatArea } from './components/ChatArea';
import { buildWorkspaceUrl } from './appMode';
import { useDashboard } from './hooks/useDashboard';
import { useSSE } from './hooks/useSSE';
import {
  createWidgetDashboardChartItem,
  type WidgetDashboardPayload,
} from './widgetDashboardSnapshot';
import {
  isWidgetMessage,
  postWidgetMessage,
  readWidgetAuthMessage,
  readWidgetEmbedContext,
} from './widgetMessageProtocol';

type DashboardTarget =
  | { mode: 'existing'; dashboardId: string }
  | { mode: 'new'; name: string };

export function WidgetApp() {
  const embedContext = useMemo(
    () => readWidgetEmbedContext(window.location.href),
    [],
  );
  const developmentMode = (
    !embedContext
    || embedContext.parentOrigin === window.location.origin
  );
  const [embedAuth, setEmbedAuth] = useState<{
    status: 'waiting' | 'authorized' | 'error';
    token: string;
    expiresAt: number;
  }>(() => ({
    status: developmentMode ? 'authorized' : 'waiting',
    token: '',
    expiresAt: 0,
  }));
  const handleAuthorizationError = useCallback(() => {
    setEmbedAuth(current => ({ ...current, status: 'error', token: '' }));
    if (embedContext) {
      postWidgetMessage(
        window.parent,
        embedContext,
        'water-agent-widget:auth-required',
      );
    }
  }, [embedContext]);
  const requestOptions = useMemo(() => {
    if (developmentMode) return undefined;
    return {
      enabled: embedAuth.status === 'authorized',
      dataSourcesEndpoint: '/api/embed/data-sources',
      chatEndpoint: '/api/embed/vanna/v2/chat_sse',
      headersProvider: () => ({
        Authorization: `Bearer ${embedAuth.token}`,
        'X-Water-Agent-Parent-Origin': embedContext!.parentOrigin,
      }),
      onAuthorizationError: handleAuthorizationError,
    };
  }, [
    developmentMode,
    embedAuth.status,
    embedAuth.token,
    embedContext,
    handleAuthorizationError,
  ]);
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
  const {
    dashboards,
    currentDashboardId,
    addItemsToDashboard,
    createDashboardWithItems,
  } = useDashboard();
  const [pendingAdd, setPendingAdd] = useState<WidgetDashboardPayload | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (!notice?.ok) return;
    const timer = window.setTimeout(() => setNotice(null), 2500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!embedContext) return;
    const handleWidgetMessage = (event: MessageEvent) => {
      const auth = readWidgetAuthMessage(
        event,
        embedContext,
        window.parent,
      );
      if (auth) {
        if (auth.expiresAt <= Math.floor(Date.now() / 1000)) {
          setEmbedAuth({ status: 'error', token: '', expiresAt: 0 });
        } else {
          setEmbedAuth({
            status: 'authorized',
            token: auth.token,
            expiresAt: auth.expiresAt,
          });
        }
        return;
      }
      if (
        !isWidgetMessage(
          event,
          embedContext,
          'water-agent-widget:opened',
          window.parent,
        )
      ) {
        return;
      }
      window.dispatchEvent(new Event('water-agent-widget:opened'));
    };
    window.addEventListener('message', handleWidgetMessage);
    postWidgetMessage(window.parent, embedContext, 'water-agent-widget:ready');
    return () => {
      window.removeEventListener('message', handleWidgetMessage);
    };
  }, [embedContext]);

  const currentSessionExists = sessionList.some(
    session => session.id === currentSessionId,
  );

  const workspaceUrl = buildWorkspaceUrl(
    window.location.origin,
    currentSessionId,
  );

  const requestWidgetClose = useCallback(() => {
    if (!embedContext) return;
    postWidgetMessage(
      window.parent,
      embedContext,
      'water-agent-widget:minimize',
    );
  }, [embedContext]);

  const handleRequestAddToDashboard = useCallback(
    (payload: WidgetDashboardPayload) => {
      setNotice(null);
      setPendingAdd(payload);
    },
    [],
  );

  const handleConfirmAddToDashboard = useCallback(
    (target: DashboardTarget) => {
      if (!pendingAdd) return;
      const item = createWidgetDashboardChartItem(
        pendingAdd,
        currentSessionId,
      );
      const targetId = target.mode === 'existing'
        ? (
            addItemsToDashboard(target.dashboardId, [item])
              ? target.dashboardId
              : null
          )
        : createDashboardWithItems(target.name, [item]);

      setPendingAdd(null);
      setNotice(targetId
        ? { ok: true, message: '已添加到仪表板' }
        : {
            ok: false,
            message: '添加失败，localStorage 可能已满或仪表板不存在，请重试。',
          });
    },
    [
      addItemsToDashboard,
      createDashboardWithItems,
      currentSessionId,
      pendingAdd,
    ],
  );

  return (
    <div className="widget-shell">
      <header className="widget-header">
        <div className="widget-title-block">
          <span className="widget-status-dot" aria-hidden="true" />
          <div>
            <strong>智能问数</strong>
            <span>水利数据助手</span>
          </div>
        </div>
        <div className="widget-header-actions">
          <button
            type="button"
            onClick={createNewSession}
            disabled={loading || embedAuth.status !== 'authorized'}
            title="新建会话"
          >
            新建
          </button>
          <a
            href={workspaceUrl}
            target="_blank"
            rel="noreferrer"
            title="在新标签页打开完整工作台"
          >
            完整工作台
          </a>
          <button
            type="button"
            className="widget-icon-button"
            onClick={requestWidgetClose}
            title="最小化"
            aria-label="最小化智能问数"
          >
            —
          </button>
        </div>
      </header>

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

      {!developmentMode && embedAuth.status !== 'authorized' && (
        <div
          className="widget-error"
          role={embedAuth.status === 'error' ? 'alert' : 'status'}
        >
          {embedAuth.status === 'error'
            ? '嵌入访问验证失败，请联系系统管理员。'
            : '正在验证嵌入访问权限'}
        </div>
      )}

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
          onAddToDashboard={handleRequestAddToDashboard}
          compact
          workspaceUrl={workspaceUrl}
          hideHeader
          disabled={embedAuth.status !== 'authorized'}
        />
      </div>

      {pendingAdd && (
        <AddToDashboardDialog
          dashboards={dashboards}
          currentDashboardId={currentDashboardId}
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
