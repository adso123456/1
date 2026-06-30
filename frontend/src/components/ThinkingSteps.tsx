import { useState } from 'react';
import type { DataFrameData } from '../types';
import { isNullValue } from '../hooks/useSSE';

interface Props {
  dataframes: DataFrameData[];
}

/** 渲染单个 DataFrame 的迷你表格 */
function MiniTable({ df }: { df: DataFrameData }) {
  return (
    <div style={{ overflowX: 'auto', marginTop: 8 }}>
      <table
        style={{
          borderCollapse: 'collapse',
          fontSize: 12,
          width: '100%',
          border: '1px solid #e5e7eb',
          borderRadius: 4,
          overflow: 'hidden',
        }}
      >
        <thead>
          <tr style={{ backgroundColor: '#f3f4f6' }}>
            {df.columns.map(col => (
              <th key={col} style={{ padding: '4px 8px', textAlign: 'left', borderBottom: '1px solid #e5e7eb', fontWeight: 500 }}>
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {df.data.slice(0, 5).map((row, ri) => (
            <tr key={ri} style={{ borderBottom: '1px solid #f3f4f6' }}>
              {df.columns.map(col => (
                <td key={col} style={{ padding: '3px 8px', color: '#6b7280' }}>
                  {isNullValue(row[col]) ? '' : String(row[col])}
                </td>
              ))}
            </tr>
          ))}
          {df.data.length > 5 && (
            <tr>
              <td colSpan={df.columns.length} style={{ padding: '4px 8px', color: '#9ca3af', fontSize: 11 }}>
                ... 共 {df.data.length} 行，仅显示前5行
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function ThinkingSteps({ dataframes }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (dataframes.length === 0) return null;

  // 只有 1 个 dataframe 且没有行数据 → 不展示
  if (dataframes.length === 1 && dataframes[0].row_count === 0) return null;

  return (
    <div style={{ marginTop: 12, fontSize: 13 }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          cursor: 'pointer',
          color: '#6b7280',
          padding: '6px 10px',
          borderRadius: 6,
          border: '1px solid #e5e7eb',
          backgroundColor: '#f9fafb',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 14 }}>{expanded ? '▾' : '▸'}</span>
        <span>思考过程</span>
        <span style={{ color: '#9ca3af' }}>
          （{dataframes.length} 次查询，{dataframes.reduce((s, d) => s + d.row_count, 0)} 行结果）
        </span>
      </div>
      {expanded && (
        <div style={{ marginTop: 8, paddingLeft: 8, borderLeft: '2px solid #e5e7eb' }}>
          {dataframes.map((df, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <div style={{ color: '#9ca3af', fontSize: 11, marginBottom: 4 }}>
                查询 {i + 1} — {df.row_count} 行 × {df.column_count} 列
              </div>
              <MiniTable df={df} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
