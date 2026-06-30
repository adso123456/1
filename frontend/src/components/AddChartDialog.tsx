import { useState, useMemo, useEffect } from 'react';
import type { SessionMeta, ChatMessage, ChartData, DataFrameData, DashboardItem, DashboardChartItem, DashboardTableItem } from '../types';
import { getSessionMessages } from '../hooks/useSSE';
import { ChartView } from './ChartView';
import { TableView } from './TableView';
import { parseMarkdownTableHeaders, buildColumnLabelMap } from '../utils/tableFormatting';

interface Props {
  sessions: SessionMeta[];
  existingIds: Set<string>;
  onAdd: (items: DashboardItem[]) => void;
  onClose: () => void;
}

interface ExtractedChart {
  kind: 'chart';
  chart: ChartData;
  sourceSessionId: string;
  sourceMessageId: string;
  dashboardId: string;
  messageText: string;
}

interface ExtractedTable {
  kind: 'table';
  dataframe: DataFrameData;
  sourceSessionId: string;
  sourceMessageId: string;
  dashboardId: string;
  messageText: string;
  /** 在该消息中的索引 */
  dfIndex: number;
  /** 是否是消息中的最后一个 dataframe */
  isLast: boolean;
}

type ExtractedItem = ExtractedChart | ExtractedTable;

/** 按消息分组的结果 */
interface MessageGroup {
  messageId: string;
  messageText: string;
  items: ExtractedItem[];
}

function isValidChart(chart: ChartData): boolean {
  if (chart.error) return false;
  if (!chart.spec || chart.spec.type === 'none') return false;
  if (!Array.isArray(chart.rows) || chart.rows.length === 0) return false;
  return true;
}

function isValidTable(df: DataFrameData): boolean {
  if (!Array.isArray(df.columns) || df.columns.length === 0) return false;
  if (!Array.isArray(df.data) || df.data.length === 0) return false;
  return true;
}

/** 通用标题模式，应视为无意义标题 */
const GENERIC_TITLE_RE = /^(query results|数据表|查询结果|表格|data table|table\s*\d*)$/i;

/** 行号列模式 — fallbackTitleFromColumns 中跳过此类列 */
const ROW_HEADER_RE = /^(#|排名|序号|序|No\.?|编号|行号)$/i;

/** 从消息文本中提取表格标题 — 查找 Markdown 表格前最近的描述性段落 */
function extractTitleFromMessageText(msgText: string): string | null {
  if (!msgText) return null;

  // 找第一个 Markdown 表格的位置，取前面的文本
  const tableIdx = msgText.search(/\n\|[^|\n]+\|/);
  const before = tableIdx === -1 ? msgText : msgText.slice(0, tableIdx);
  const lines = before.split('\n').filter(l => l.trim());
  if (lines.length === 0) return null;

  // 从后往前找第一个有实际内容的行（跳过标题标记和分隔线）
  for (let i = lines.length - 1; i >= 0; i--) {
    let line = lines[i].trim();
    line = line.replace(/^#+\s*/, '');
    if (line.length <= 1 || /^[-─—=_*]{3,}$/.test(line)) continue;

    line = line
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/\*(.+?)\*/g, '$1')
      .replace(/`(.+?)`/g, '$1')
      .replace(/^[:：，,、\s]+/, '')
      .replace(/[:：，,、。；;！!？?]+\s*$/, '')
      .replace(/^(以下是|下面是|如下[：:]?|查询结果[：:]?)\s*/i, '')
      .trim();

    if (line.length >= 2) {
      return line.length > 36 ? line.slice(0, 34) + '…' : line;
    }
  }

  return null;
}

/** 基于列名生成回退标题 — 优先 columnLabels，其次中文字段名 */
function fallbackTitleFromColumns(
  columns: string[],
  columnLabels?: Record<string, string>,
): string {
  if (columnLabels) {
    const labels = Object.values(columnLabels);
    const contentLabel = labels.find(l => !ROW_HEADER_RE.test(l));
    if (contentLabel) return contentLabel;
  }
  const firstCol = columns[0];
  if (firstCol && /[一-鿿]/.test(firstCol)) return firstCol;
  return '数据表';
}

/** 三级优先级生成表格标题 */
function generateTableTitle(
  df: DataFrameData,
  msgText: string,
  isLast: boolean,
  columnLabels?: Record<string, string>,
): string {
  // 1. 原始 title 有效且非泛型 → 直接用
  const rawTitle = df.title?.trim();
  if (rawTitle && !GENERIC_TITLE_RE.test(rawTitle)) return rawTitle;

  // 2. 最后一个 DataFrame → 尝试从消息文本提取
  if (isLast) {
    const msgTitle = extractTitleFromMessageText(msgText);
    if (msgTitle) return msgTitle;
  }

  // 3. 基于列名生成
  return fallbackTitleFromColumns(df.columns, columnLabels);
}

/** 从消息 Markdown 文本中提取所有表格的表头（按出现顺序） */
function extractAllMarkdownHeaders(markdown: string): string[][] {
  if (!markdown) return [];
  const lines = markdown.split('\n');
  const headers: string[][] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line.startsWith('|') || !line.endsWith('|')) continue;
    const nextLine = i + 1 < lines.length ? lines[i + 1].trim() : '';
    if (!/^\|[\s\-:|]+\|$/.test(nextLine)) continue;

    const cols = line
      .split('|')
      .map(s => s.trim())
      .filter(s => s !== '');
    if (cols.length > 0) headers.push(cols);
  }

  return headers;
}

/** 从消息列表中提取全部有效图表和表格，按消息分组 */
function extractItems(messages: ChatMessage[], sessionId: string): MessageGroup[] {
  const groups: MessageGroup[] = [];

  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;

    const groupItems: ExtractedItem[] = [];

    // 提取该消息中所有 Markdown 表头（与 dataframe 位置对应）
    const allHeaders = extractAllMarkdownHeaders(msg.text);

    // 遍历全部 dataframes
    for (let i = 0; i < msg.dataframes.length; i++) {
      const df = msg.dataframes[i];
      if (!isValidTable(df)) continue;

      // 尝试从 Markdown 表头建立列标签映射
      const isLast = i === msg.dataframes.length - 1;
      let columnLabels: Record<string, string> | undefined;

      if (isLast && allHeaders.length > 0) {
        // 最后一个 dataframe → 用最后一个 Markdown 表头
        columnLabels = buildColumnLabelMap(df.columns, allHeaders[allHeaders.length - 1]);
      } else if (i < allHeaders.length) {
        // 中间 dataframe → 只有同等位置存在独立且列数匹配的 Markdown 表头时才映射
        columnLabels = buildColumnLabelMap(df.columns, allHeaders[i]);
      }
      // 无可靠表头时 columnLabels 保持 undefined

      const title = generateTableTitle(df, msg.text, isLast, columnLabels);
      groupItems.push({
        kind: 'table',
        dataframe: {
          ...df,
          title,
          columnLabels: columnLabels ?? df.columnLabels,
        },
        sourceSessionId: sessionId,
        sourceMessageId: msg.id,
        dashboardId: `${sessionId}::${msg.id}::table::${i}`,
        messageText: msg.text.slice(0, 60) || '（无文本）',
        dfIndex: i,
        isLast,
      });
    }

    // 遍历全部 charts
    for (const chart of msg.charts) {
      if (!isValidChart(chart)) continue;

      groupItems.push({
        kind: 'chart',
        chart,
        sourceSessionId: sessionId,
        sourceMessageId: msg.id,
        dashboardId: `${sessionId}::${msg.id}::${chart.id}`,
        messageText: msg.text.slice(0, 60) || '（无文本）',
      });
    }

    if (groupItems.length > 0) {
      groups.push({
        messageId: msg.id,
        messageText: msg.text.slice(0, 80) || '（无文本）',
        items: groupItems,
      });
    }
  }

  return groups;
}

const SESSION_BG = '#f8f9fb';
const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_BG = '#eef2ff';
const ACTIVE_TEXT = '#2563eb';

export function AddChartDialog({ sessions, existingIds, onAdd, onClose }: Props) {
  const sorted = useMemo(
    () => [...sessions].sort((a, b) => b.updatedAt - a.updatedAt),
    [sessions],
  );

  const [selectedSessionId, setSelectedSessionId] = useState<string>(
    sorted.length > 0 ? sorted[0].id : '',
  );
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const messages = useMemo<ChatMessage[]>(() => {
    if (!selectedSessionId) return [];
    return getSessionMessages(selectedSessionId);
  }, [selectedSessionId]);

  const groups = useMemo<MessageGroup[]>(
    () => extractItems(messages, selectedSessionId),
    [messages, selectedSessionId],
  );

  // 切换会话时重置选择
  useEffect(() => {
    setSelectedIds(new Set());
  }, [selectedSessionId]);

  const toggleItem = (dashboardId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(dashboardId)) {
        next.delete(dashboardId);
      } else {
        next.add(dashboardId);
      }
      return next;
    });
  };

  const handleConfirm = () => {
    if (selectedIds.size === 0) return;

    const items: DashboardItem[] = [];
    for (const group of groups) {
      for (const item of group.items) {
        if (!selectedIds.has(item.dashboardId)) continue;

        if (item.kind === 'chart') {
          const ci: DashboardChartItem = {
            type: 'chart',
            id: item.dashboardId,
            sourceSessionId: item.sourceSessionId,
            sourceMessageId: item.sourceMessageId,
            addedAt: Date.now(),
            chart: JSON.parse(JSON.stringify(item.chart)),
          };
          items.push(ci);
        } else {
          const ti: DashboardTableItem = {
            type: 'table',
            id: item.dashboardId,
            sourceSessionId: item.sourceSessionId,
            sourceMessageId: item.sourceMessageId,
            addedAt: Date.now(),
            table: JSON.parse(JSON.stringify(item.dataframe)),
          };
          items.push(ti);
        }
      }
    }
    onAdd(items);
  };

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  };

  // 统计图表和表格总数
  const totalChartCount = groups.reduce((s, g) => s + g.items.filter(i => i.kind === 'chart').length, 0);
  const totalTableCount = groups.reduce((s, g) => s + g.items.filter(i => i.kind === 'table').length, 0);

  return (
    <div
      onClick={handleBackdropClick}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        backgroundColor: 'rgba(0,0,0,0.35)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          width: '90vw',
          maxWidth: 960,
          height: '80vh',
          maxHeight: 700,
          backgroundColor: '#fff',
          borderRadius: 12,
          boxShadow: '0 8px 40px rgba(0,0,0,0.18)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* 标题栏 */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 20px',
          borderBottom: `1px solid ${BORDER}`,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: TEXT_PRIMARY }}>
            从历史会话添加图表和表格
          </span>
          <button
            onClick={onClose}
            title="关闭"
            aria-label="关闭"
            style={{
              width: 28,
              height: 28,
              border: 'none',
              borderRadius: 6,
              backgroundColor: 'transparent',
              color: TEXT_MUTED,
              cursor: 'pointer',
              fontSize: 18,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            ×
          </button>
        </div>

        {/* 主体 */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* 左侧：会话列表 */}
          <div style={{
            width: 240,
            minWidth: 240,
            borderRight: `1px solid ${BORDER}`,
            backgroundColor: SESSION_BG,
            overflowY: 'auto',
            padding: '8px 0',
          }}>
            <div style={{
              fontSize: 11,
              fontWeight: 500,
              color: TEXT_MUTED,
              padding: '6px 16px 8px',
              letterSpacing: '0.5px',
            }}>
              历史会话
            </div>
            {sorted.length === 0 && (
              <div style={{ fontSize: 12, color: TEXT_MUTED, padding: '20px 16px', textAlign: 'center' }}>
                暂无会话
              </div>
            )}
            {sorted.map(s => {
              const active = s.id === selectedSessionId;
              return (
                <div
                  key={s.id}
                  onClick={() => setSelectedSessionId(s.id)}
                  title={s.title}
                  style={{
                    padding: '8px 16px',
                    fontSize: 13,
                    fontWeight: active ? 500 : 400,
                    color: active ? ACTIVE_TEXT : TEXT_SECONDARY,
                    backgroundColor: active ? ACTIVE_BG : 'transparent',
                    cursor: 'pointer',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    transition: 'background-color .12s',
                  }}
                  onMouseEnter={e => {
                    if (!active) e.currentTarget.style.backgroundColor = '#f3f4f6';
                  }}
                  onMouseLeave={e => {
                    if (!active) e.currentTarget.style.backgroundColor = 'transparent';
                  }}
                >
                  {s.title}
                </div>
              );
            })}
          </div>

          {/* 右侧：按消息分组展示 */}
          <div style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 20px',
          }}>
            {groups.length === 0 ? (
              <div style={{
                color: TEXT_MUTED,
                fontSize: 13,
                textAlign: 'center',
                paddingTop: 80,
              }}>
                {messages.length === 0
                  ? '该会话没有聊天记录'
                  : '该会话中没有可用的表格或图表'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
                {groups.map((group, gi) => (
                  <div key={group.messageId}>
                    {/* 消息分组标题 */}
                    <div style={{
                      fontSize: 12,
                      fontWeight: 500,
                      color: TEXT_MUTED,
                      marginBottom: 8,
                      padding: '4px 0',
                      borderBottom: `1px solid ${BORDER}`,
                    }}>
                      回答 {gi + 1}
                      <span style={{ marginLeft: 8, fontWeight: 400, color: '#c4c4c4' }}>
                        {group.messageText}
                      </span>
                    </div>

                    {/* 该消息下的所有候选 */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {group.items.map(item => {
                        const alreadyAdded = existingIds.has(item.dashboardId);
                        const checked = selectedIds.has(item.dashboardId);

                        const typeLabel = item.kind === 'chart' ? '图表' : '表格';
                        const typeColor = item.kind === 'chart' ? '#2563eb' : '#16a34a';

                        // 表格标题
                        let itemTitle: string;
                        if (item.kind === 'chart') {
                          itemTitle = item.chart.title || '图表';
                        } else {
                          itemTitle = item.dataframe.title || `数据表 ${item.dfIndex + 1}`;
                        }

                        // 副标题
                        let subtitle: string;
                        if (item.kind === 'chart') {
                          subtitle = item.messageText;
                        } else {
                          const tag = item.isLast ? '最终表格' : '过程表格';
                          subtitle = `${tag} · ${item.dataframe.data.length} 行 × ${item.dataframe.columns.length} 列`;
                        }

                        return (
                          <div
                            key={item.dashboardId}
                            onClick={() => {
                              if (!alreadyAdded) toggleItem(item.dashboardId);
                            }}
                            title={alreadyAdded ? '已加入仪表板' : '点击选择'}
                            style={{
                              display: 'flex',
                              gap: 12,
                              border: `1px solid ${checked ? ACTIVE_TEXT : BORDER}`,
                              borderRadius: 8,
                              padding: 12,
                              cursor: alreadyAdded ? 'default' : 'pointer',
                              backgroundColor: checked ? '#f0f4ff' : alreadyAdded ? '#fafafa' : '#fff',
                              opacity: alreadyAdded ? 0.6 : 1,
                              transition: 'border-color .12s, background-color .12s',
                            }}
                          >
                            {/* 复选框 */}
                            <div style={{ flexShrink: 0, paddingTop: 2 }}>
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={alreadyAdded}
                                onChange={() => {
                                  if (!alreadyAdded) toggleItem(item.dashboardId);
                                }}
                                style={{ cursor: alreadyAdded ? 'not-allowed' : 'pointer' }}
                              />
                            </div>

                            {/* 信息 + 预览 */}
                            <div style={{ flex: 1, minWidth: 0 }}>
                              {/* 标题行 */}
                              <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 8,
                                marginBottom: 4,
                              }}>
                                <span style={{
                                  display: 'inline-block',
                                  fontSize: 10,
                                  fontWeight: 500,
                                  color: typeColor,
                                  backgroundColor: item.kind === 'chart' ? '#dbeafe' : '#dcfce7',
                                  padding: '1px 6px',
                                  borderRadius: 3,
                                  flexShrink: 0,
                                }}>
                                  {typeLabel}
                                </span>
                                <span style={{
                                  fontSize: 13,
                                  fontWeight: 500,
                                  color: TEXT_PRIMARY,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}>
                                  {itemTitle}
                                </span>
                              </div>

                              {/* 副标题 */}
                              <div style={{
                                fontSize: 11,
                                color: TEXT_MUTED,
                                marginBottom: 8,
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}>
                                {subtitle}
                                {alreadyAdded && (
                                  <span style={{ marginLeft: 8, color: '#16a34a', fontWeight: 500 }}>
                                    已添加
                                  </span>
                                )}
                              </div>

                              {/* 预览 */}
                              <div style={{
                                maxHeight: item.kind === 'chart' ? 200 : 160,
                                overflow: 'hidden',
                                pointerEvents: 'none',
                                opacity: 0.9,
                              }}>
                                {item.kind === 'chart' ? (
                                  <ChartView chart={item.chart} hideTitle hideTableToggle />
                                ) : (
                                  <TableView table={item.dataframe} preview />
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* 统计摘要 */}
            {groups.length > 0 && (
              <div style={{
                marginTop: 16,
                fontSize: 11,
                color: TEXT_MUTED,
                textAlign: 'center',
              }}>
                共 {totalTableCount} 张表格、{totalChartCount} 张图表
              </div>
            )}
          </div>
        </div>

        {/* 底部按钮 */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '12px 20px',
          borderTop: `1px solid ${BORDER}`,
          flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            style={{
              padding: '8px 20px',
              border: `1px solid ${BORDER}`,
              borderRadius: 6,
              backgroundColor: '#fff',
              color: TEXT_SECONDARY,
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={selectedIds.size === 0}
            title={selectedIds.size === 0 ? '请先选择表格或图表' : `添加 ${selectedIds.size} 项到仪表板`}
            style={{
              padding: '8px 20px',
              border: 'none',
              borderRadius: 6,
              backgroundColor: selectedIds.size > 0 ? ACTIVE_TEXT : '#d1d5db',
              color: '#fff',
              cursor: selectedIds.size > 0 ? 'pointer' : 'not-allowed',
              fontSize: 13,
              fontWeight: 500,
              transition: 'background-color .15s',
            }}
          >
            添加{selectedIds.size > 0 ? ` (${selectedIds.size})` : ''}
          </button>
        </div>
      </div>
    </div>
  );
}
