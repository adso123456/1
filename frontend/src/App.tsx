import { useState, useMemo, useCallback } from 'react';
import { ChatArea } from './components/ChatArea';
import { Sidebar } from './components/Sidebar';
import { DashboardView } from './components/DashboardView';
import { DashboardListPanel } from './components/DashboardListPanel';
import { AddChartDialog } from './components/AddChartDialog';
import { useSSE } from './hooks/useSSE';
import { useDashboard } from './hooks/useDashboard';
import type { DashboardItem } from './types';

type View = 'chat' | 'dashboard';

function App() {
  const { messages, loading, sendMessage, cancelRequest, clearMessages, sessionList, currentSessionId, createNewSession, switchToSession, deleteSession } = useSSE();
  const {
    currentItems: dashboardItems,
    currentDashboardName,
    dashboards,
    currentDashboardId,
    addItems,
    removeItem,
    updateLayout,
    createDashboard,
    switchDashboard,
  } = useDashboard();

  const [currentView, setCurrentView] = useState<View>('chat');
  const [showAddDialog, setShowAddDialog] = useState(false);

  // 已加入仪表板的项目 ID 集合（用于对话框去重展示）
  const existingIds = useMemo(
    () => new Set(dashboardItems.map(c => c.id)),
    [dashboardItems],
  );

  // 切换会话时自动切回对话视图
  const handleSwitchSession = useCallback((id: string) => {
    setCurrentView('chat');
    switchToSession(id);
  }, [switchToSession]);

  // 新建会话时自动切回对话视图
  const handleNewSession = useCallback(() => {
    setCurrentView('chat');
    createNewSession();
  }, [createNewSession]);

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
      <div style={{ flex: 1, minWidth: 0 }}>
        {currentView === 'chat' ? (
          <ChatArea
            messages={messages}
            loading={loading}
            onSend={sendMessage}
            onCancel={cancelRequest}
            onClear={clearMessages}
            onChangeChartType={() => {}}
          />
        ) : (
          <DashboardView
            items={dashboardItems}
            dashboardName={currentDashboardName}
            onRemove={handleRemoveItem}
            onAddChart={() => setShowAddDialog(true)}
            onLayoutChange={updateLayout}
          />
        )}
      </div>

      {/* 添加图表和表格弹窗 */}
      {showAddDialog && (
        <AddChartDialog
          sessions={sessionList}
          existingIds={existingIds}
          onAdd={handleAddItems}
          onClose={() => setShowAddDialog(false)}
        />
      )}
    </div>
  );
}

export default App;
