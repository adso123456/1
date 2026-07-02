import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { DashboardItem } from '../types';
import { default as GridLayout } from 'react-grid-layout/legacy';
import type { Layout } from 'react-grid-layout/legacy';
import { ChartView } from './ChartView';
import { TableView } from './TableView';
import 'react-grid-layout/css/styles.css';
import { exportDashboardAsPng, generateExportFilename } from '../utils/dashboardExport';

interface Props {
  items: DashboardItem[];
  dashboardName: string;
  onRemove: (id: string) => void;
  onAddChart: () => void;
  onLayoutChange: (layout: Layout) => void;
  /** 仪表板单项高度校正：写入指定项目 layout.h（保留 x/y/w） */
  onUpdateItemHeight?: (id: string, h: number) => void;
}

const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_TEXT = '#2563eb';

const COLS = 6;
const ROW_HEIGHT = 100;
const GRID_MARGIN = 16;
/** 表格卡片标题栏 + 卡片边框等固定占用高度（标题栏 padding 10*2 + ×按钮 28 + borderBottom 1 + 卡片 border 2 ≈ 51）。
 *  表格内容区 padding 为 0，故不另加。 */
const TABLE_CARD_CHROME = 51;

export function DashboardView({ items, dashboardName, onRemove, onAddChart, onLayoutChange, onUpdateItemHeight }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(600);
  const [exporting, setExporting] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  // 表格实测内容高度（仅 table 自然高度）：TableView 上报，用于换算网格高度
  const [tableHeights, setTableHeights] = useState<Record<string, number>>({});

  /** TableView 上报表格内容高度：更新本地状态并校正持久化的 layout.h。
   *  幂等：相同高度不会触发持久化写入（useDashboard.updateItemHeight 内部去重）。
   *  身份稳定（仅依赖 onUpdateItemHeight），供多个表格共享一个回调。 */
  const handleTableHeight = useCallback((id: string, contentHeight: number) => {
    setTableHeights(prev => {
      if (prev[id] === contentHeight) return prev; // 未变化不重渲染
      return { ...prev, [id]: contentHeight };
    });
    if (onUpdateItemHeight) {
      // 卡片所需像素高度 = 表格内容高度 + 标题栏/边框等固定占用
      const requiredPx = contentHeight + TABLE_CARD_CHROME;
      // 换算为网格高度：grid 单元像素高度 = h*ROW_HEIGHT + (h-1)*MARGIN = h*(ROW_HEIGHT+MARGIN) - MARGIN
      // 解 requiredPx <= h*(ROW_HEIGHT+MARGIN) - MARGIN 得 h >= (requiredPx + MARGIN) / (ROW_HEIGHT + MARGIN)
      const gridH = Math.ceil((requiredPx + GRID_MARGIN) / (ROW_HEIGHT + GRID_MARGIN));
      onUpdateItemHeight(id, gridH);
    }
  }, [onUpdateItemHeight]);

  // 通过 ResizeObserver 获取实际可用宽度，替代 window.innerWidth 硬编码减法
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        // 减去网格容器左右 padding 各 20px
        setContainerWidth(entry.contentRect.width - 40);
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const chartCount = items.filter(i => i.type === 'chart').length;
  const tableCount = items.filter(i => i.type === 'table').length;

  const parts: string[] = [];
  if (chartCount > 0) parts.push(`${chartCount} 张图表`);
  if (tableCount > 0) parts.push(`${tableCount} 张表格`);
  const summary = parts.length > 0 ? parts.join('，') : '暂无内容';

  /** 把表格实测内容高度换算为网格高度（与 handleTableHeight 内公式一致） */
  const tableGridHeight = useCallback((contentHeight: number): number => {
    const requiredPx = contentHeight + TABLE_CARD_CHROME;
    return Math.ceil((requiredPx + GRID_MARGIN) / (ROW_HEIGHT + GRID_MARGIN));
  }, []);

  const layout: Layout = useMemo(
    () => items.map(di => {
      const savedH = di.layout?.h;
      // 图表默认 h=4；新表格临时默认 h=2，挂载后由实测高度校正
      const defaultH = di.type === 'chart' ? 4 : 2;
      // 表格：若已有实测内容高度，按内容换算网格高度，并设 minH 防止缩到内容以下
      if (di.type === 'table') {
        const measured = tableHeights[di.id];
        if (measured && measured > 0) {
          const contentH = tableGridHeight(measured);
          return {
            i: di.id,
            x: di.layout?.x ?? 0,
            y: di.layout?.y ?? 0,
            w: di.layout?.w ?? 3,
            h: contentH,
            minH: contentH,
          };
        }
      }
      return {
        i: di.id,
        x: di.layout?.x ?? 0,
        y: di.layout?.y ?? 0,
        w: di.layout?.w ?? 3,
        h: savedH ?? defaultH,
      };
    }),
    [items, tableHeights, tableGridHeight],
  );

  const handleLayoutChange = useCallback((newLayout: Layout) => {
    onLayoutChange(newLayout);
  }, [onLayoutChange]);

  const handleExport = useCallback(async () => {
    if (!contentRef.current || exporting) return;
    setExporting(true);
    try {
      await exportDashboardAsPng(contentRef.current, generateExportFilename());
    } catch (err) {
      console.error('仪表板导出失败:', err);
      alert('导出图片失败，请稍后重试');
    } finally {
      setExporting(false);
    }
  }, [exporting]);

  return (
    <div
      ref={rootRef}
      style={{
        height: '100vh',
        overflowY: 'auto',
        backgroundColor: '#f5f5f5',
      }}
    >
      {/* 完整内容容器：供导出，随内容自然撑开，不设固定高度 */}
      <div ref={contentRef} data-export-root>
        {/* 顶部栏 */}
      <header data-export-header style={{
        padding: '12px 20px',
        backgroundColor: '#fff',
        borderBottom: `1px solid ${BORDER}`,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: TEXT_PRIMARY }}>
            {dashboardName}
          </h1>
          <p style={{ margin: '2px 0 0', fontSize: 12, color: TEXT_MUTED }}>
            {summary}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <button
            data-export-exclude
            disabled={items.length === 0 || exporting}
            onClick={handleExport}
            style={{
              padding: '8px 16px',
              border: `1px solid ${BORDER}`,
              borderRadius: 6,
              backgroundColor: '#fff',
              color: exporting ? TEXT_MUTED : TEXT_PRIMARY,
              cursor: items.length === 0 || exporting ? 'not-allowed' : 'pointer',
              fontSize: 13,
              fontWeight: 500,
              transition: 'all .15s',
              opacity: items.length === 0 ? 0.5 : 1,
            }}
          >
            {exporting ? '导出中…' : '导出图片'}
          </button>
          <button
            data-export-exclude
            onClick={onAddChart}
            style={{
              padding: '8px 18px',
              border: 'none',
              borderRadius: 6,
              backgroundColor: ACTIVE_TEXT,
              color: '#fff',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 500,
              transition: 'all .15s',
            }}
          >
            + 添加
          </button>
        </div>
      </header>

      {items.length === 0 ? (
        <div style={{
          textAlign: 'center',
          paddingTop: 120,
          color: TEXT_MUTED,
        }}>
          <div style={{ fontSize: 48, marginBottom: 16, opacity: 0.3 }}>📊</div>
          <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 8, color: TEXT_SECONDARY }}>
            仪表板为空
          </div>
          <div style={{ fontSize: 13, marginBottom: 20 }}>
            从历史会话中选择表格和图表加入仪表板
          </div>
          <button
            data-export-exclude
            onClick={onAddChart}
            style={{
              padding: '10px 24px',
              border: `1px dashed ${ACTIVE_TEXT}`,
              borderRadius: 8,
              backgroundColor: '#eef2ff',
              color: ACTIVE_TEXT,
              cursor: 'pointer',
              fontSize: 14,
              fontWeight: 500,
              transition: 'all .15s',
            }}
          >
            + 添加
          </button>
        </div>
      ) : (
        <div style={{ padding: 20, minHeight: 'calc(100vh - 60px)' }}>
          <GridLayout
            className="dashboard-grid"
            layout={layout}
            cols={COLS}
            rowHeight={ROW_HEIGHT}
            width={containerWidth}
            compactType="vertical"
            preventCollision={false}
            margin={[16, 16]}
            containerPadding={[0, 0]}
            isDraggable={true}
            isResizable={true}
            resizeHandles={['se']}
            draggableHandle=".dashboard-card-drag-handle"
            draggableCancel="button"
            onLayoutChange={handleLayoutChange}
          >
            {items.map(di => {
              const isChart = di.type === 'chart';
              const rawTitle = isChart ? di.chart.title : di.table.title;
              const itemTitle = rawTitle && rawTitle !== 'Query Results'
                ? rawTitle
                : isChart ? '图表' : '数据表';

              return (
                <div
                  data-export-card
                  data-export-card-id={di.id}
                  key={di.id}
                  style={{
                    border: `1px solid ${BORDER}`,
                    borderRadius: 10,
                    backgroundColor: '#fff',
                    overflow: 'hidden',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
                    display: 'flex',
                    flexDirection: 'column',
                    height: '100%',
                  }}
                >
                  {/* 卡片标题栏（可拖拽） */}
                  <div
                    className="dashboard-card-drag-handle"
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '10px 16px',
                      borderBottom: `1px solid #f3f4f6`,
                      backgroundColor: '#fafafa',
                      cursor: 'grab',
                      userSelect: 'none',
                      flexShrink: 0,
                    }}
                  >
                    <span style={{
                      fontSize: 14,
                      fontWeight: 500,
                      color: TEXT_PRIMARY,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      flex: 1,
                      minWidth: 0,
                    }}>
                      {itemTitle}
                    </span>
                    <button
                      data-export-exclude
                      onClick={() => {
                        if (window.confirm(`确定从仪表板移除「${itemTitle}」吗？`)) {
                          onRemove(di.id);
                        }
                      }}
                      title="从仪表板移除"
                      aria-label={`移除${isChart ? '图表' : '表格'}「${itemTitle}」`}
                      style={{
                        flexShrink: 0,
                        width: 28,
                        height: 28,
                        marginLeft: 8,
                        border: 'none',
                        borderRadius: 4,
                        backgroundColor: 'transparent',
                        color: TEXT_MUTED,
                        cursor: 'pointer',
                        fontSize: 16,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        opacity: 0.5,
                        transition: 'opacity .12s, background-color .12s, color .12s',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.backgroundColor = '#fee2e2';
                        e.currentTarget.style.color = '#dc2626';
                        e.currentTarget.style.opacity = '1';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.backgroundColor = 'transparent';
                        e.currentTarget.style.color = TEXT_MUTED;
                        e.currentTarget.style.opacity = '0.5';
                      }}
                    >
                      ×
                    </button>
                  </div>

                  {/* 内容 */}
                  <div style={{
                    flex: 1,
                    display: 'flex',
                    flexDirection: 'column',
                    minHeight: 0,
                    // 表格完整展开（dashboardMode 自身 overflow:visible 不滚动），
                    // 仅图表需要 hidden 裁切 ECharts 容器
                    overflow: isChart ? 'hidden' : 'visible',
                    padding: isChart ? '8px 12px 12px' : 0,
                  }}>
                    {isChart ? (
                      <ChartView chart={di.chart} hideTitle hideTableToggle hideDescription fillHeight showExport />
                    ) : (
                      <TableView
                        table={di.table}
                        hideFooter
                        dashboardMode
                        itemId={di.id}
                        onContentHeightChange={handleTableHeight}
                      />
                    )}
                  </div>
                </div>
              );
            })}
          </GridLayout>
        </div>
      )}
      </div>
    </div>
  );
}
