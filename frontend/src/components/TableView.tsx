import type { DataFrameData } from '../types';
import { formatCellValue, formatColumnLabel } from '../utils/tableFormatting';

interface Props {
  table: DataFrameData;
  /** 预览模式：只展示前 5 行 */
  preview?: boolean;
  /** 隐藏底部统计行（仪表板精简用） */
  hideFooter?: boolean;
}

export function TableView({ table, preview, hideFooter }: Props) {
  const rows = preview ? table.data.slice(0, 5) : table.data;

  return (
    <div style={{
      maxHeight: preview ? 160 : 400,
      overflowY: 'auto',
      overflowX: 'auto',
      border: preview ? 'none' : '1px solid #e5e7eb',
      borderRadius: preview ? 0 : 6,
    }}>
      <table style={{
        borderCollapse: 'collapse',
        fontSize: 12,
        width: '100%',
        minWidth: table.columns.length * 100,
      }}>
        <thead>
          <tr style={{ backgroundColor: '#f3f4f6', position: 'sticky', top: 0, zIndex: 1 }}>
            <th style={{
              padding: '6px 8px',
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
                padding: '6px 12px',
                textAlign: 'left',
                borderBottom: '2px solid #e5e7eb',
                fontWeight: 500,
                whiteSpace: 'nowrap',
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
                padding: '5px 8px',
                textAlign: 'center',
                color: '#9ca3af',
                fontSize: 11,
                whiteSpace: 'nowrap',
              }}>
                {ri + 1}
              </td>
              {table.columns.map(col => (
                <td key={col} style={{
                  padding: '5px 12px',
                  color: '#374151',
                  whiteSpace: 'nowrap',
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
