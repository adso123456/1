import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import type { DashboardItem, ChartSpec, ChartData } from '../types';
import { default as GridLayout } from 'react-grid-layout/legacy';
import type { Layout, LayoutItem } from 'react-grid-layout/legacy';
import { ChartView } from './ChartView';
import { TableView } from './TableView';
import 'react-grid-layout/css/styles.css';
import {
  downloadDashboardPng,
  generateExportFilename,
  renderDashboardAsPng,
} from '../utils/dashboardExport';

interface Props {
  items: DashboardItem[];
  dashboardName: string;
  onRemove: (id: string) => void;
  onAddChart: () => void;
  onLayoutChange: (layout: Layout) => void;
  /** 仪表板单项高度校正：写入指定项目 layout.h（保留 x/y/w） */
  onUpdateItemHeight?: (id: string, h: number) => void;
  /** 用户手动缩放表格卡片后回传项目 ID，供上层持久化（自动贴合不再覆盖该卡片高度） */
  onUpdateItemSized?: (id: string) => void;
  /** 仪表板内图表切换类型后，回传项目 ID 与完整 ChartSpec 供上层持久化 */
  onUpdateChartSpec?: (id: string, spec: ChartSpec) => void;
  /** 仪表板内 V2 图表切换：回传项目 ID 与完整新 ChartData（含 transform 后 columns/rows/v2Meta） */
  onV2ChartSwitch?: (dashboardItemId: string, newChart: ChartData) => void;
}

const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_TEXT = '#2563eb';

const COLS = 6;
// 细粒度纵向网格：减少表格底部量化留白（量化误差 ≤ ROW_HEIGHT+GRID_MARGIN-1 = 15px）
const ROW_HEIGHT = 10;
const GRID_MARGIN = 6;
/** 表格卡片固定占用高度的兜底估算：标题栏(padding 10*2 + ×按钮 28 + borderBottom 1 ≈ 49) + 卡片 border 2 + 表格容器 border 2 ≈ 53。
 *  仅在标题栏实测高度尚未就绪时作为 fallback，实测（headerHeights）就绪后优先用实测值。 */
const TABLE_CARD_CHROME = 53;

export function DashboardView({ items, dashboardName, onRemove, onAddChart, onLayoutChange, onUpdateItemHeight, onUpdateItemSized, onUpdateChartSpec, onV2ChartSwitch }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(600);
  const [exporting, setExporting] = useState(false);
  const [exportPreview, setExportPreview] = useState<{ blob: Blob; url: string } | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => {
    if (exportPreview?.url) URL.revokeObjectURL(exportPreview.url);
  }, [exportPreview?.url]);

  // 表格实测内容高度（仅 table 自然高度）：TableView 上报，用于换算网格高度
  const [tableHeights, setTableHeights] = useState<Record<string, number>>({});

  // 各卡片标题栏实测高度（含 borderBottom）：替代硬编码 TABLE_CARD_CHROME 估算。
  // ref 回调在 render 时读取，高度稳定（标题栏 flexShrink:0 + nowrap），
  // 同值 setState 返回原引用不触发重渲染，一次收敛无循环。
  const [headerHeights, setHeaderHeights] = useState<Record<string, number>>({});
  const headerRefCb = useCallback((id: string) => (el: HTMLDivElement | null) => {
    if (!el) return;
    const h = Math.round(el.getBoundingClientRect().height);
    setHeaderHeights(prev => (prev[id] === h ? prev : { ...prev, [id]: h }));
  }, []);

  // 用户手动缩放过的表格卡片 id：从 items.userSized 派生（随 item 持久化，删除卡片/仪表板自动清理）。
  // 标记后自动贴合不再覆盖其高度（表格在卡片内滚动）；未缩放过的表格始终自动贴合内容高度。
  const manuallySized = useMemo(
    () => new Set(items.filter(i => i.type === 'table' && i.userSized).map(i => i.id)),
    [items],
  );

  /** 表格内容高度 → 网格高度换算。chrome = 标题栏实测高度 + 卡片 border 2；实测未就绪时用兜底常量。
   *  换算依据：grid 单元像素高度 = h*ROW_HEIGHT + (h-1)*MARGIN = h*(ROW_HEIGHT+MARGIN) - MARGIN，
   *  解 requiredPx <= h*(ROW_HEIGHT+MARGIN) - MARGIN 得 h >= (requiredPx + MARGIN) / (ROW_HEIGHT + MARGIN)。 */
  const contentHeightToGrid = useCallback((contentHeight: number, headerH?: number): number => {
    // chrome = 标题栏实测 + 卡片 border 上下 2 + 表格容器 border 上下 2；实测未就绪时用兜底常量
    const chrome = headerH && headerH > 0 ? headerH + 4 : TABLE_CARD_CHROME;
    const requiredPx = contentHeight + chrome;
    return Math.ceil((requiredPx + GRID_MARGIN) / (ROW_HEIGHT + GRID_MARGIN));
  }, []);

  /** TableView 上报表格内容高度：更新本地状态并校正持久化 layout.h。
   *  自动贴合（未手动缩放过的卡片）：内容高度变化时始终贴合，吸收宽度/数据变化；
   *  用户手动缩放过的卡片尊重其高度（表格在卡片内滚动），自动贴合不再覆盖。 */
  const handleTableHeight = useCallback((id: string, contentHeight: number) => {
    setTableHeights(prev => {
      if (prev[id] === contentHeight) return prev; // 未变化不重渲染
      return { ...prev, [id]: contentHeight };
    });
    if (onUpdateItemHeight && !manuallySized.has(id)) {
      const gridH = contentHeightToGrid(contentHeight, headerHeights[id]);
      onUpdateItemHeight(id, gridH);
    }
  }, [onUpdateItemHeight, manuallySized, headerHeights, contentHeightToGrid]);

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
  const tableGridHeight = useCallback(
    (contentHeight: number, headerH?: number): number =>
      contentHeightToGrid(contentHeight, headerH),
    [contentHeightToGrid],
  );

  const layout: Layout = useMemo(
    () => items.map(di => {
      const savedH = di.layout?.h;
      // 默认高度（细粒度网格）：图表 ≈ 原 448px → h=29；表格 ≈ 原 216px → h=14，挂载后由实测高度校正
      const defaultH = di.type === 'chart' ? 29 : 14;
      // 表格：若已有实测内容高度，按内容换算网格高度；
      // 用户手动缩放过的 → 尊重其高度（表格在卡片内滚动）；
      // 未手动缩放的 → 贴合内容高度；
      // minH 允许缩到内容高度以下，超出部分由表格容器滚动承接
      if (di.type === 'table') {
        const measured = tableHeights[di.id];
        if (measured && measured > 0) {
          const contentH = tableGridHeight(measured, headerHeights[di.id]);
          const effectiveH = manuallySized.has(di.id) && savedH && savedH > 0 ? savedH : contentH;
          return {
            i: di.id,
            x: di.layout?.x ?? 0,
            y: di.layout?.y ?? 0,
            w: di.layout?.w ?? 3,
            h: effectiveH,
            minH: Math.max(1, Math.min(contentH, 4)),
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
    [items, tableHeights, headerHeights, manuallySized, tableGridHeight],
  );

  const handleLayoutChange = useCallback((newLayout: Layout) => {
    onLayoutChange(newLayout);
  }, [onLayoutChange]);

  /** GridLayout 用户拖拽缩放结束：标记用户手动缩放的表格，之后自动贴合不再覆盖其高度（表格在卡片内滚动） */
  const handleResizeStop = useCallback((_layout: Layout, _oldItem: LayoutItem | null, newItem: LayoutItem | null) => {
    if (!newItem) return;
    const it = items.find(i => i.id === newItem.i);
    if (it?.type === 'table') {
      onUpdateItemSized?.(newItem.i);
    }
  }, [items, onUpdateItemSized]);

  const handleExport = useCallback(async () => {
    if (!contentRef.current || exporting) return;
    setExporting(true);
    try {
      const blob = await renderDashboardAsPng(contentRef.current);
      setExportPreview({ blob, url: URL.createObjectURL(blob) });
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
            {exporting ? '生成中…' : '导出预览'}
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
            margin={[16, GRID_MARGIN]}
            containerPadding={[0, 0]}
            isDraggable={true}
            isResizable={true}
            resizeHandles={['se']}
            draggableHandle=".dashboard-card-drag-handle"
            draggableCancel="button"
            onLayoutChange={handleLayoutChange}
            onResizeStop={handleResizeStop}
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
                    minWidth: 0,
                  }}
                >
                  {/* 卡片标题栏（可拖拽）；ref 回调实测高度用于换算网格高度 */}
                  <div
                    ref={headerRefCb(di.id)}
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
                    minWidth: 0,
                    // 裁切溢出：图表裁切 ECharts 容器，表格由 TableView 根容器内部滚动承接
                    overflow: 'hidden',
                    padding: isChart ? '8px 12px 12px' : 0,
                  }}>
                    {isChart ? (
                      <ChartView
                        chart={di.chart}
                        hideTitle
                        hideTableToggle
                        hideDescription
                        fillHeight
                        messageId={di.id}
                        chartIndex={0}
                        onChangeSpec={(spec) => onUpdateChartSpec?.(di.id, spec)}
                        onV2ChartSwitch={onV2ChartSwitch
                          ? (_, __, newChart) => onV2ChartSwitch(di.id, newChart)
                          : undefined}
                      />
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
      {exportPreview && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="仪表板导出预览"
          style={{
            position: 'fixed',
            zIndex: 1000,
            inset: 0,
            padding: 24,
            background: 'rgb(15 23 42 / 60%)',
          }}
        >
          <div style={{
            display: 'flex',
            width: 'min(1280px, 100%)',
            height: '100%',
            margin: '0 auto',
            flexDirection: 'column',
            overflow: 'hidden',
            borderRadius: 10,
            background: '#fff',
          }}>
            <header style={{
              display: 'flex',
              minHeight: 58,
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 16,
              padding: '0 18px',
              borderBottom: '1px solid #e2e8f0',
              background: '#fff',
            }}>
              <div>
                <strong style={{ color: TEXT_PRIMARY }}>仪表板导出预览</strong>
                <span style={{ marginLeft: 12, color: TEXT_MUTED, fontSize: 12 }}>
                  {dashboardName}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => downloadDashboardPng(exportPreview.blob, generateExportFilename())}
                  style={{
                    border: 0,
                    borderRadius: 6,
                    padding: '8px 14px',
                    background: ACTIVE_TEXT,
                    color: '#fff',
                    cursor: 'pointer',
                  }}
                >
                  下载 PNG
                </button>
                <button
                  onClick={() => setExportPreview(null)}
                  style={{
                    border: `1px solid ${BORDER}`,
                    borderRadius: 6,
                    padding: '8px 14px',
                    background: '#fff',
                    color: TEXT_PRIMARY,
                    cursor: 'pointer',
                  }}
                >
                  关闭
                </button>
              </div>
            </header>
            <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 20, background: '#e2e8f0' }}>
              <img
                src={exportPreview.url}
                alt={`${dashboardName}导出预览`}
                style={{ display: 'block', maxWidth: '100%', margin: '0 auto', background: '#fff', boxShadow: '0 4px 18px rgb(15 23 42 / 18%)' }}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
