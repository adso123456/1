import { ChatArea } from './components/ChatArea';
import { buildWorkspaceUrl } from './appMode';
import { useSSE } from './hooks/useSSE';

function requestWidgetClose() {
  window.parent.postMessage(
    { type: 'water-agent-widget:close' },
    window.location.origin,
  );
}

export function WidgetApp() {
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
  } = useSSE();

  const currentSessionExists = sessionList.some(
    session => session.id === currentSessionId,
  );

  const workspaceUrl = buildWorkspaceUrl(window.location.origin);

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
            disabled={loading}
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
          compact
          hideHeader
        />
      </div>
    </div>
  );
}
