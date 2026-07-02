import { useRef, useLayoutEffect, useCallback } from 'react';
import type { DataFrameData } from '../types';
import { formatCellValue, formatColumnLabel } from '../utils/tableFormatting';

interface Props {
  table: DataFrameData;
  /** 预览模式：只展示前 5 行 */
  preview?: boolean;
  /** 隐藏底部统计行（仪表板精简用） */
  hideFooter?: boolean;
  /** 仪表板模式：表格完整展开、不产生滚动条，由父容器按内容高度自适应；
   *  未传时保持聊天页/弹窗原有的滚动 + maxHeight 行为不变 */
  dashboardMode?: boolean;
  /** 仪表板模式专用：表格内容高度（px）变化时回调，供父容器校正网格高度。
   *  回调身份应保持稳定（useCallback），避免 ResizeObserver effect 频繁重建。
   *  仅在高度真正变化时触发，避免重复回调导致渲染循环 */
  onContentHeightChange?: (itemId: string, height: number) => void;
  /** 仪表板模式专用：本项目 ID，回调时透传，便于父容器定位 */
  itemId?: string;
}

export function TableView({ table, preview, hideFooter, dashboardMode, onContentHeightChange, itemId }: Props) {
  const rows = preview ? table.data.slice(0, 5) : table.data;

  // 仪表板模式下测量表格完整高度，去重后上报，避免渲染循环
  const tableRef = useRef<HTMLTableElement>(null);
  const lastReportedRef = useRef<number>(-1);
  // 用 ref 持有最新的 itemId/回调，使 reportHeight 身份稳定，ResizeObserver effect 不频繁重建
  const reportCtxRef = useRef<{ id?: string; cb?: (id: string, h: number) => void }>({});
  reportCtxRef.current = { id: itemId, cb: onContentHeightChange };

  const reportHeight = useCallback(() => {
    if (!dashboardMode) return;
    const el = tableRef.current;
    if (!el) return;
    const h = Math.round(el.getBoundingClientRect().height);
    if (h <= 0) return;
    if (h === lastReportedRef.current) return; // 相同高度不重复回调
    lastReportedRef.current = h;
    const { id, cb } = reportCtxRef.current;
    if (id && cb) cb(id, h);
  }, [dashboardMode]);

  // 初次布局及数据变化后测量
  useLayoutEffect(() => {
    reportHeight();
  }, [reportHeight, table.data, table.columns]);

  // 仪表板模式下：表格宽度受容器约束，文本换行会导致高度变化，用 ResizeObserver 监听
  useLayoutEffect(() => {
    if (!dashboardMode) return;
    const el = tableRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      reportHeight();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [dashboardMode, reportHeight]);

  // 仪表板模式：根容器不产生滚动条，表格完整展开
  const rootStyle = dashboardMode
    ? {
        height: 'auto',
        maxHeight: 'none' as const,
        minHeight: 0,
        overflow: 'visible' as const,
        border: preview ? 'none' : '1px solid #e5e7eb',
        borderRadius: preview ? 0 : 6,
      }
    : {
        maxHeight: preview ? 160 : 400,
        overflowY: 'auto' as const,
        overflowX: 'auto' as const,
        border: preview ? 'none' : '1px solid #e5e7eb',
        borderRadius: preview ? 0 : 6,
      };

  const tableStyle = dashboardMode
    ? {
        borderCollapse: 'collapse' as const,
        fontSize: 12,
        width: '100%',
        minWidth: 0,
        tableLayout: 'fixed' as const,
      }
    : {
        borderCollapse: 'collapse' as const,
        fontSize: 12,
        width: '100%',
        minWidth: table.columns.length * 100,
      };

  return (
    <div style={rootStyle}>
      <table ref={tableRef} style={tableStyle}>
        <thead>
          <tr style={{
            backgroundColor: '#f3f4f6',
            // 仪表板模式表头不 sticky（无滚动容器，sticky 无意义且会脱离表格）
            ...(dashboardMode ? {} : { position: 'sticky', top: 0, zIndex: 1 }),
          }}>
            <th style={{
              padding: dashboardMode ? '4px 6px' : '6px 8px',
              textAlign: 'center',
              borderBottom: '2px solid #e5e7eb',
              fontWeight: 500,
              color: '#9ca3af',
              width: 40,
              whiteSpace: 'nowrap',
            }}>
              #
            </th>
            {table.columns.map(col => (
              <th key={col} style={{
                padding: dashboardMode ? '4px 8px' : '6px 12px',
                textAlign: 'left',
                borderBottom: '2px solid #e5e7eb',
                fontWeight: 500,
                ...(dashboardMode
                  ? { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const }
                  : { whiteSpace: 'nowrap' as const }),
              }}>
                {formatColumnLabel(col, table.columnLabels)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri} style={{ borderBottom: '1px solid #f3f4f6' }}>
              <td style={{
                padding: dashboardMode ? '3px 6px' : '5px 8px',
                textAlign: 'center',
                color: '#9ca3af',
                fontSize: 11,
                whiteSpace: 'nowrap',
              }}>
                {ri + 1}
              </td>
              {table.columns.map(col => (
                <td key={col} style={{
                  padding: dashboardMode ? '3px 8px' : '5px 12px',
                  color: '#374151',
                  ...(dashboardMode
                    ? { whiteSpace: 'normal' as const, overflowWrap: 'anywhere' as const }
                    : { whiteSpace: 'nowrap' as const }),
                }}>
                  {formatCellValue(row[col])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      {preview && table.data.length > 5 && (
        <div style={{
          padding: '6px 12px',
          color: '#9ca3af',
          fontSize: 11,
          textAlign: 'center',
          backgroundColor: '#fafafa',
          borderTop: '1px solid #f3f4f6',
        }}>
          ... 共 {table.data.length} 行（仅预览前 5 行）
        </div>
      )}

      {!preview && !hideFooter && (
        <div style={{
          padding: '6px 12px',
          color: '#9ca3af',
          fontSize: 11,
          backgroundColor: '#fafafa',
          borderTop: '1px solid #e5e7eb',
          position: 'sticky',
          bottom: 0,
        }}>
          共 {table.data.length} 行 × {table.columns.length} 列
        </div>
      )}
    </div>
  );
}
