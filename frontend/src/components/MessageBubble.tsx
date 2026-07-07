import { useState, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage, ChartData, RenderableChartType } from '../types';
import { buildChartOption } from '../chartRegistry';
import { ThinkingSteps } from './ThinkingSteps';
import { ChartView } from './ChartView';
import { ChartErrorBoundary } from './ChartCard';

interface Props {
  message: ChatMessage;
  onChangeChartType?: (type: RenderableChartType) => void;
  /** V2 图表切换：基于 sourceColumns/sourceRows 重新执行 V2 plan+transform，返回完整 ChartData */
  onV2ChartSwitch?: (newChart: ChartData) => void;
  /** 点击"添加到仪表板"时回调，携带当前 activeSpec 图表快照、消息 ID、消息 SQL（会话 ID 由 App 补充） */
  onAddToDashboard?: (payload: { chart: ChartData; messageId: string; sql: string | null }) => void;
}

/** 去除图表注释标记及流式未闭合残片，避免显示在正文中 */
function cleanMarkdown(text: string): string {
  return text
    .replace(/<!--\s*chart_spec:\s*[\s\S]*?\s*-->/gi, '')
    .replace(/<!--\s*chart_type:\s*\w*\s*-->/gi, '')
    .replace(/<!--[\s\S]*$/, '')
    .trimEnd();
}

export function MessageBubble({ message, onChangeChartType, onV2ChartSwitch, onAddToDashboard }: Props) {
  const isUser = message.role === 'user';
  const hasSql = !!(message.sql && message.sql.trim());
  const [showSql, setShowSql] = useState(false);
  const [copyLabel, setCopyLabel] = useState('复制 SQL');

  const handleCopy = useCallback(async () => {
    if (!message.sql) return;
    try {
      await navigator.clipboard.writeText(message.sql);
      setCopyLabel('已复制');
    } catch {
      setCopyLabel('复制失败');
    }
    setTimeout(() => setCopyLabel('复制 SQL'), 2000);
  }, [message.sql]);

  // 纯 loading 态：轻量行内指示器，不用大气泡卡片
  if (!isUser && message.streaming && !message.text) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <span className="typing-dots">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </span>
        <span style={{ color: '#9ca3af', fontSize: 13 }}>思考中…</span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 16,
      }}
    >
      <div
        style={{
          width: isUser ? undefined : '100%',
          maxWidth: '85%',
          padding: isUser ? '10px 16px' : '16px 20px',
          borderRadius: 12,
          backgroundColor: isUser ? '#2563eb' : '#ffffff',
          color: isUser ? '#ffffff' : '#1f2937',
          border: isUser ? 'none' : '1px solid #e5e7eb',
          boxShadow: isUser ? 'none' : '0 1px 3px rgba(0,0,0,0.06)',
          lineHeight: 1.7,
        }}
      >
        {isUser ? (
          <div style={{ whiteSpace: 'pre-wrap' }}>{message.text}</div>
        ) : (
          <>
            {/* 思考过程（中间步骤，默认折叠） */}
            {message.dataframes.length > 1 && (
              <ThinkingSteps dataframes={message.dataframes.slice(0, -1)} />
            )}

            {/* Markdown 正文 */}
            {message.text && (
              <div className="markdown-body" style={{ fontSize: 14 }}>
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    table: ({ children }) => (
                      <table style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0' }}>
                        {children}
                      </table>
                    ),
                    th: ({ children }) => (
                      <th style={{
                        padding: '6px 12px',
                        textAlign: 'left',
                        borderBottom: '2px solid #e5e7eb',
                        backgroundColor: '#f9fafb',
                        fontWeight: 500,
                        fontSize: 13,
                      }}>
                        {children}
                      </th>
                    ),
                    td: ({ children }) => (
                      <td style={{
                        padding: '5px 12px',
                        borderBottom: '1px solid #f3f4f6',
                        fontSize: 13,
                      }}>
                        {children}
                      </td>
                    ),
                  }}
                >
                  {cleanMarkdown(message.text)}
                </ReactMarkdown>
              </div>
            )}

            {/* 视图切换：图表 / 表格 / SQL */}
            {hasSql && (
              <div style={{ display: 'flex', gap: 4, border: '1px solid #e5e7eb', borderRadius: 6, padding: 3, width: 'fit-content', marginBottom: 12 }}>
                <button
                  onClick={() => setShowSql(false)}
                  style={{
                    padding: '4px 14px',
                    border: 'none',
                    borderRadius: 4,
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: !showSql ? 600 : 400,
                    backgroundColor: !showSql ? '#2563eb' : 'transparent',
                    color: !showSql ? '#fff' : '#6b7280',
                    transition: 'all .15s',
                  }}
                >
                  图表 / 表格
                </button>
                <button
                  onClick={() => setShowSql(true)}
                  style={{
                    padding: '4px 14px',
                    border: 'none',
                    borderRadius: 4,
                    cursor: 'pointer',
                    fontSize: 12,
                    fontWeight: showSql ? 600 : 400,
                    backgroundColor: showSql ? '#2563eb' : 'transparent',
                    color: showSql ? '#fff' : '#6b7280',
                    transition: 'all .15s',
                  }}
                >
                  SQL
                </button>
              </div>
            )}

            {/* SQL 视图 */}
            {showSql && hasSql && (
              <div
                style={{
                  backgroundColor: '#1e293b',
                  borderRadius: 8,
                  padding: 16,
                  marginBottom: 8,
                  position: 'relative',
                }}
              >
                <pre
                  style={{
                    color: '#e2e8f0',
                    fontSize: 13,
                    fontFamily: '"Cascadia Code", "Fira Code", "JetBrains Mono", Consolas, monospace',
                    lineHeight: 1.6,
                    whiteSpace: 'pre',
                    overflowX: 'auto',
                    margin: 0,
                    paddingBottom: 36,
                  }}
                >
                  {message.sql}
                </pre>
                <button
                  onClick={handleCopy}
                  style={{
                    position: 'absolute',
                    bottom: 8,
                    right: 12,
                    padding: '4px 12px',
                    border: '1px solid #475569',
                    borderRadius: 4,
                    backgroundColor: '#334155',
                    color: '#cbd5e1',
                    fontSize: 12,
                    cursor: 'pointer',
                    transition: 'all .15s',
                  }}
                >
                  {copyLabel}
                </button>
              </div>
            )}

            {/* 图表区域（SQL 模式下用 display:none 保留内部状态） */}
            {/* 多条 charts 时，只展示第一张无 error 且可构建的图表；无有效图表则展示第一张错误图表 */}
            {(() => {
              // 选择展示图表：第一张无 error 且可构建 > 第一张有 error > 第一张
              let best = message.charts.length > 0 ? message.charts[0] : null;
              if (message.charts.length > 1) {
                const valid = message.charts.find(c => !c.error && buildChartOption(c) !== null);
                if (valid) {
                  best = valid;
                } else {
                  const errChart = message.charts.find(c => !!c.error);
                  if (errChart) best = errChart;
                }
              }
              if (!best) return null;

              return (
                <div style={{ display: showSql ? 'none' : undefined }}>
                  <ChartErrorBoundary
                    resetKey={`${best.id}|${best.error ?? ''}|${JSON.stringify(best.spec)}|${best.columns.join(',')}|${best.dataVersion}`}
                    fallback={
                      <div
                        style={{
                          color: '#ef4444',
                          fontSize: 13,
                          padding: '12px',
                          backgroundColor: '#fef2f2',
                          borderRadius: 6,
                          border: '1px solid #fecaca',
                        }}
                      >
                        图表渲染失败
                      </div>
                    }
                  >
                    <ChartView
                      chart={best}
                      onChangeType={onChangeChartType}
                      onV2ChartSwitch={onV2ChartSwitch}
                      showExport
                      onAddToDashboard={onAddToDashboard
                        ? (chart) => onAddToDashboard({
                            chart,
                            messageId: message.id,
                            sql: message.sql ?? null,
                          })
                        : undefined}
                    />
                  </ChartErrorBoundary>
                </div>
              );
            })()}
          </>
        )}
      </div>
    </div>
  );
}
