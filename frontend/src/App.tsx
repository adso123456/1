import { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { ChatArea } from './components/ChatArea';
import { Sidebar } from './components/Sidebar';
import { DashboardView } from './components/DashboardView';
import { DashboardListPanel } from './components/DashboardListPanel';
import { AddChartDialog } from './components/AddChartDialog';
import { AddToDashboardDialog } from './components/AddToDashboardDialog';
import { AssistantManagement } from './AdminApp';
import { DataSourcePage } from './components/DataSourcePage';
import { NewConversationSourceDialog } from './components/NewConversationSourceDialog';
import {
  ReportPreviewModal,
  type ReportOptions,
  type ReportResultData,
} from './components/ReportComponents';
import { configFromReportResult } from './reportConfigState';
import { useSSE } from './hooks/useSSE';
import { useDashboard } from './hooks/useDashboard';
import type { DashboardItem, DashboardChartItem, ChartData, ChartSpec, SuggestedQuestion, SuggestedQuestionsResponse } from './types';
import {
  clearWorkspaceSessionParam,
  readWorkspaceSessionId,
} from './appMode';
import { dataSourceUnavailableReason } from './dataSourceState';
import {
  formatDatabaseType,
  formatDataSourceStatus,
} from './dataSourcePresentation';

type View = 'chat' | 'datasource' | 'dashboard' | 'assistant';

/** 待添加到仪表板的图表上下文（点击"添加到仪表板"时暂存） */
interface PendingAdd {
  chart: ChartData;
  messageId: string;
  sql: string | null;
  sessionId: string;
}

/** 顶部 Toast 状态 */
interface ToastState {
  message: string;
  /** 成功时为可跳转的目标仪表板 ID；失败时为 null（不显示"打开仪表板"） */
  dashboardId: string | null;
}

function App() {
  const requestedSessionId = useMemo(
    () => readWorkspaceSessionId(window.location.href),
    [],
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
    clearStorageError,
    dataSources,
    refreshDataSources,
    currentSourceId,
    dataSourceError,
    sourceBound,
  } = useSSE(requestedSessionId);
  const {
    currentItems: dashboardItems,
    currentDashboardName,
    dashboards,
    currentDashboardId,
    addItems,
    removeItem,
    updateLayout,
    updateItemHeight,
    updateChartSpec,
    updateChartFull,
    createDashboard,
    switchDashboard,
    addItemsToDashboard,
    createDashboardWithItems,
  } = useDashboard();

  const [currentView, setCurrentView] = useState<View>('chat');
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [pendingAdd, setPendingAdd] = useState<PendingAdd | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [reportPreview, setReportPreview] = useState<ReportResultData | null>(null);
  const [showSourceDialog, setShowSourceDialog] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestedQuestion[]>([]);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 新会话（已绑定数据源且无消息）时，按会话绑定拉取数据源专属推荐问题
  useEffect(() => {
    if (!currentSessionId || !sourceBound || messages.length > 0) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    fetch(`/api/conversations/${encodeURIComponent(currentSessionId)}/suggested-questions`)
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
  }, [currentSessionId, messages.length, sourceBound]);

  useEffect(() => {
    if (!requestedSessionId || currentSessionId !== requestedSessionId) return;
    window.history.replaceState(
      null,
      '',
      clearWorkspaceSessionParam(window.location.href),
    );
  }, [currentSessionId, requestedSessionId]);

  // 已加入仪表板的项目 ID 集合（用于对话框去重展示）
  const existingIds = useMemo(
    () => new Set(dashboardItems.map(c => c.id)),
    [dashboardItems],
  );

  // 成功 Toast：2.5 秒自动关闭（失败 Toast 不自动关闭，需手动关闭）
  useEffect(() => {
    if (!toast) return;
    if (toast.dashboardId === null) return; // 失败提示不自动关闭
    toastTimerRef.current = setTimeout(() => setToast(null), 2500);
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, [toast]);

  // 切换会话时自动切回对话视图
  const handleSwitchSession = useCallback((id: string) => {
    setCurrentView('chat');
    switchToSession(id);
  }, [switchToSession]);

  // 新建会话时自动切回对话视图
  const handleNewSession = useCallback(() => {
    setCurrentView('chat');
    setShowSourceDialog(true);
  }, []);

  const currentSource = dataSources.find(source => source.source_id === currentSourceId);
  const sourceUnavailable = dataSourceUnavailableReason(
    currentSource,
    sourceBound,
  );

  useEffect(() => {
    if (!pendingQuestion || !sourceBound || !currentSourceId || messages.length > 0) return;
    const question = pendingQuestion;
    setPendingQuestion(null);
    void sendMessage(question);
  }, [currentSourceId, messages.length, pendingQuestion, sendMessage, sourceBound]);

  useEffect(() => {
    if (
      currentView === 'chat'
      && dataSources.length > 0
      && messages.length === 0
      && !sourceBound
    ) {
      setShowSourceDialog(true);
    }
  }, [currentView, dataSources.length, messages.length, sourceBound]);

  // 添加项目到仪表板
  const handleAddItems = useCallback((items: DashboardItem[]) => {
    const ok = addItems(items);
    if (!ok) {
      alert('写入失败，localStorage 可能已满。请清理部分数据后重试。');
    } else {
      setShowAddDialog(false);
    }
  }, [addItems]);

  // 从仪表板移除项目
  const handleRemoveItem = useCallback((id: string) => {
    const ok = removeItem(id);
    if (!ok) {
      alert('写入失败，localStorage 可能已满。请清理部分数据后重试。');
    }
  }, [removeItem]);

  // 仪表板内图表切换类型后持久化完整 spec（旧路径，保留兼容）
  const handleUpdateChartSpec = useCallback((id: string, spec: ChartSpec) => {
    const ok = updateChartSpec(id, spec);
    if (!ok) {
      alert('图表配置保存失败，localStorage 可能已满。');
    }
  }, [updateChartSpec]);

  // 仪表板内 V2 图表切换：持久化完整 ChartData（含 transform 后 columns/rows/v2Meta/source 数据）
  const handleDashboardV2ChartSwitch = useCallback((dashboardItemId: string, newChart: ChartData) => {
    const ok = updateChartFull(dashboardItemId, newChart);
    if (!ok) {
      alert('图表切换保存失败，localStorage 可能已满。');
    }
  }, [updateChartFull]);

  // 聊天页"添加到仪表板"：暂存快照 + 上下文（会话 ID 在此补充），打开弹窗
  const handleRequestAddToDashboard = useCallback((payload: { chart: ChartData; messageId: string; sql: string | null }) => {
    setPendingAdd({
      chart: payload.chart,
      messageId: payload.messageId,
      sql: payload.sql,
      sessionId: currentSessionId,
    });
  }, [currentSessionId]);

  // 弹窗确认：构造 DashboardChartItem 并写入目标仪表板（已有 upsert / 新建）
  const handleConfirmAddToDashboard = useCallback((target: { mode: 'existing'; dashboardId: string } | { mode: 'new'; name: string }) => {
    if (!pendingAdd) return;
    const { chart, messageId, sql, sessionId } = pendingAdd;
    const now = Date.now();

    // 项目 ID 规则与 AddChartDialog 一致：${sessionId}::${messageId}::${chart.id}
    const item: DashboardChartItem = {
      type: 'chart',
      id: `${sessionId}::${messageId}::${chart.id}`,
      sourceSessionId: sessionId,
      sourceMessageId: messageId,
      addedAt: now,
      chart: JSON.parse(JSON.stringify(chart)), // 深拷贝当前 activeSpec 快照
      sourceSql: sql ?? null,
      lastRefreshedAt: now,
    };

    let targetId: string | null = null;
    if (target.mode === 'existing') {
      const ok = addItemsToDashboard(target.dashboardId, [item]);
      targetId = ok ? target.dashboardId : null;
    } else {
      targetId = createDashboardWithItems(target.name, [item]);
    }

    setPendingAdd(null); // 关闭弹窗（含提交态）

    if (targetId) {
      // 添加成功：不离开聊天页，显示成功 Toast
      setToast({ message: '添加成功', dashboardId: targetId });
    } else {
      // 写入失败：明确提示，不得静默失败
      setToast({ message: '添加失败，localStorage 可能已满或仪表板不存在，请稍后重试。', dashboardId: null });
    }
  }, [pendingAdd, addItemsToDashboard, createDashboardWithItems]);

  // Toast"打开仪表板"：切换目标仪表板并跳到仪表板视图
  const handleOpenDashboard = useCallback(() => {
    if (!toast?.dashboardId) return;
    switchDashboard(toast.dashboardId);
    setCurrentView('dashboard');
    setToast(null);
  }, [toast, switchDashboard]);

  const handleReportReconfigure = useCallback(async (
    result: ReportResultData,
  ) => {
    try {
      const response = await fetch('/api/reports/water-quality/options');
      if (!response.ok) throw new Error();
      const options = await response.json() as ReportOptions;
      updatePendingReportConfig(configFromReportResult(result, options));
    } catch {
      setToast({
        message: '最新报表筛选项加载失败，请稍后重试。',
        dashboardId: null,
      });
    }
  }, [updatePendingReportConfig]);

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      <Sidebar
        sessions={sessionList}
        currentSessionId={currentSessionId}
        loading={loading}
        currentView={currentView}
        onViewChange={setCurrentView}
        onNewSession={handleNewSession}
        onSwitchSession={handleSwitchSession}
        onDeleteSession={deleteSession}
      />
      {currentView === 'dashboard' && (
        <DashboardListPanel
          dashboards={dashboards}
          currentDashboardId={currentDashboardId}
          onSwitch={switchDashboard}
          onCreate={createDashboard}
        />
      )}
      <div style={{ flex: 1, minWidth: 0, height: '100%' }}>
        {/* Chat localStorage 读写失败提示（不影响 Dashboard Toast） */}
        {currentView === 'chat' && (storageError || dataSourceError) && (
          <div style={{
            backgroundColor: '#fef3c7',
            borderBottom: '1px solid #f59e0b',
            color: '#92400e',
            padding: '8px 16px',
            fontSize: 13,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexShrink: 0,
          }}>
            <span>⚠ {storageError || dataSourceError}</span>
            <button
              onClick={clearStorageError}
              aria-label="关闭提示"
              style={{
                border: 'none',
                background: 'transparent',
                cursor: 'pointer',
                fontSize: 16,
                color: '#92400e',
                padding: '0 4px',
                lineHeight: 1,
              }}
            >
              ×
            </button>
          </div>
        )}
        {currentView === 'chat' && (
          <ChatArea
            messages={messages}
            loading={loading}
            onSend={sendMessage}
            onCancel={cancelRequest}
            onClear={clearMessages}
            onChangeChartType={() => {}}
            onV2ChartSwitch={replaceMessageChart}
            onAddToDashboard={handleRequestAddToDashboard}
            onReportGenerated={(messageId, result) => {
              replaceMessageReport(messageId, result as unknown as Record<string, unknown>);
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
            sourceLabel={currentSource
              ? `${currentSource.display_name} · ${formatDatabaseType(currentSource.database_type)} · ${formatDataSourceStatus(currentSource.status, currentSource.enabled_for_chat)}`
              : ''}
            sourceUnavailableReason={sourceUnavailable}
            dataSources={dataSources}
            suggestions={suggestions}
            onDataSourceSuggestion={async (sourceId, question) => {
              const ok = await createNewSession(sourceId);
              if (ok) setPendingQuestion(question);
              return ok;
            }}
          />
        )}
        {currentView === 'datasource' && (
          <DataSourcePage onDataSourcesChanged={refreshDataSources} />
        )}
        {currentView === 'dashboard' && (
          <DashboardView
            items={dashboardItems}
            dashboardName={currentDashboardName}
            onRemove={handleRemoveItem}
            onAddChart={() => setShowAddDialog(true)}
            onLayoutChange={updateLayout}
            onUpdateItemHeight={updateItemHeight}
            onUpdateChartSpec={handleUpdateChartSpec}
            onV2ChartSwitch={handleDashboardV2ChartSwitch}
          />
        )}
        <div
          style={{
            display: currentView === 'assistant' ? 'block' : 'none',
            height: '100%',
          }}
        >
          <AssistantManagement embedded />
        </div>
      </div>

      <ReportPreviewModal
        result={reportPreview}
        onClose={() => setReportPreview(null)}
      />

      {showSourceDialog && (
        <NewConversationSourceDialog
          sources={dataSources.filter(source => source.status === 'ready' && source.enabled_for_chat)}
          onConfirm={sourceId => createNewSession(sourceId)}
          onClose={() => setShowSourceDialog(false)}
        />
      )}

      {/* 添加图表和表格弹窗 */}
      {showAddDialog && (
        <AddChartDialog
          sessions={sessionList}
          existingIds={existingIds}
          onAdd={handleAddItems}
          onClose={() => setShowAddDialog(false)}
        />
      )}

      {/* 聊天页"添加到仪表板"弹窗 */}
      {pendingAdd && (
        <AddToDashboardDialog
          dashboards={dashboards}
          currentDashboardId={currentDashboardId}
          onConfirm={handleConfirmAddToDashboard}
          onClose={() => setPendingAdd(null)}
        />
      )}

      {/* 顶部成功/失败 Toast */}
      {toast && (
        <div style={{
          position: 'fixed',
          top: 16,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 1100,
          backgroundColor: toast.dashboardId ? '#16a34a' : '#dc2626',
          color: '#fff',
          padding: '10px 16px',
          borderRadius: 8,
          boxShadow: '0 4px 16px rgba(0,0,0,0.18)',
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          maxWidth: '90vw',
        }}>
          <span>{toast.message}</span>
          {toast.dashboardId && (
            <button
              onClick={handleOpenDashboard}
              style={{
                border: '1px solid rgba(255,255,255,0.6)',
                borderRadius: 4,
                backgroundColor: 'rgba(255,255,255,0.15)',
                color: '#fff',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 500,
                padding: '3px 10px',
              }}
            >
              打开仪表板
            </button>
          )}
          <button
            onClick={() => setToast(null)}
            aria-label="关闭提示"
            style={{
              border: 'none',
              backgroundColor: 'transparent',
              color: '#fff',
              cursor: 'pointer',
              fontSize: 16,
              lineHeight: 1,
              padding: 0,
              opacity: 0.85,
            }}
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}

export default App;
