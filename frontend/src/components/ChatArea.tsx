import { useState, useRef, useEffect } from 'react';
import type { ChatMessage, ChartData, DataSourceSummary, RenderableChartType } from '../types';
import { MessageBubble } from './MessageBubble';
import type {
  ReportConfigData,
  ReportResultData,
} from './ReportComponents';
import { ReportComposerPanel } from './ReportComposerPanel';

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
  onClear: () => void;
  onChangeChartType: (type: RenderableChartType) => void;
  /** 透传给 MessageBubble：V2 图表切换 */
  onV2ChartSwitch?: (messageId: string, chartIndex: number, newChart: ChartData) => void;
  /** 透传给 MessageBubble：点击"添加到仪表板" */
  onAddToDashboard?: (payload: { chart: ChartData; messageId: string; sql: string | null }) => void;
  /** 浮窗中的紧凑布局。 */
  compact?: boolean;
  /** 浮窗图表工具栏的完整工作台地址。 */
  workspaceUrl?: string;
  /** 由浮窗外层提供顶栏时隐藏默认顶栏。 */
  hideHeader?: boolean;
  /** 鉴权或其他外层状态未就绪时禁用发送。 */
  disabled?: boolean;
  welcome?: string;
  welcomeDescription?: string;
  theme?: string;
  onReportGenerated?: (messageId: string, result: ReportResultData) => void;
  onReportPreview?: (result: ReportResultData) => void;
  onReportReconfigure?: (result: ReportResultData) => void;
  pendingReportConfig?: ReportConfigData | null;
  onReportConfigChange?: (config: ReportConfigData) => void;
  onReportConfigCancel?: () => void;
  onReportConfigGenerated?: (result: ReportResultData) => void;
  reportRequestHeaders?: () => Record<string, string>;
  sourceLabel?: string;
  sourceUnavailableReason?: string;
  dataSources?: DataSourceSummary[];
  onDataSourceSuggestion?: (
    sourceId: string,
    question: string,
  ) => Promise<boolean> | boolean;
}

const SUGGESTIONS = [
  '生成2025年7月28日水质日报',
  '夷陵区有哪些排污口？只列前5条',
  '统计各区县排污口数量，用图表展示',
  '查询2025年1月的监测数据，只取pH值有记录的前5条',
];

export function ChatArea({
  messages,
  loading,
  onSend,
  onCancel,
  onClear,
  onChangeChartType,
  onV2ChartSwitch,
  onAddToDashboard,
  compact = false,
  workspaceUrl,
  hideHeader = false,
  disabled = false,
  welcome = '有什么可以帮助你的？',
  welcomeDescription = '用中文自然语言提问，Agent 自动查询数据库并返回图表',
  theme = '#2563eb',
  onReportGenerated,
  onReportPreview,
  onReportReconfigure,
  pendingReportConfig,
  onReportConfigChange,
  onReportConfigCancel,
  onReportConfigGenerated,
  reportRequestHeaders,
  sourceLabel,
  sourceUnavailableReason = '',
  dataSources = [],
  onDataSourceSuggestion,
}: Props) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || loading || disabled || sourceUnavailableReason) return;
    setInput('');
    onSend(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: compact ? '100%' : '100vh',
      minHeight: 0,
      backgroundColor: '#f5f5f5',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    }} className={compact ? 'chat-area chat-area--compact' : 'chat-area'}>
      {/* 顶部栏 */}
      {!hideHeader && <header style={{
        padding: '12px 20px',
        backgroundColor: '#fff',
        borderBottom: '1px solid #e5e7eb',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#1f2937' }}>
            智能问答
          </h1>
          <p style={{ margin: '2px 0 0', fontSize: 12, color: '#9ca3af' }}>
            {sourceLabel ? `数据源：${sourceLabel}` : '历史会话未绑定数据源'}
          </p>
        </div>
        <button
          onClick={onClear}
          style={{
            padding: '6px 14px',
            border: '1px solid #e5e7eb',
            borderRadius: 6,
            backgroundColor: '#fff',
            cursor: 'pointer',
            fontSize: 12,
            color: '#6b7280',
          }}
        >
          清空对话
        </button>
      </header>}

      {/* 消息列表 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: compact ? '12px' : '20px 24px' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', paddingTop: compact ? 28 : 80 }}>
            <h2 style={{ fontSize: 18, color: '#374151', fontWeight: 500, marginBottom: 8 }}>
              {welcome}
            </h2>
            <p style={{ fontSize: 13, color: '#9ca3af', marginBottom: 24 }}>
              {welcomeDescription}
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => { onSend(s); setInput(''); }}
                  disabled={disabled || Boolean(sourceUnavailableReason)}
                  style={{
                    padding: '8px 16px',
                    border: '1px solid #e5e7eb',
                    borderRadius: 20,
                    backgroundColor: '#fff',
                    cursor: 'pointer',
                    fontSize: 13,
                    color: '#374151',
                    transition: 'all .15s',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.borderColor = theme)}
                  onMouseLeave={e => (e.currentTarget.style.borderColor = '#e5e7eb')}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => (
          <MessageBubble
            key={msg.id}
            message={msg}
            onChangeChartType={onChangeChartType}
            onV2ChartSwitch={onV2ChartSwitch}
            onAddToDashboard={onAddToDashboard}
            compact={compact}
            workspaceUrl={workspaceUrl}
            onReportGenerated={onReportGenerated}
            onReportPreview={onReportPreview}
            onReportReconfigure={onReportReconfigure}
            onDataSourceSuggestion={onDataSourceSuggestion}
            dataSources={dataSources}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区 */}
      <div style={{
        padding: compact ? 10 : '16px 24px',
        backgroundColor: '#fff',
        borderTop: '1px solid #e5e7eb',
        flexShrink: 0,
      }}>
        {pendingReportConfig && onReportConfigChange && onReportConfigCancel && onReportConfigGenerated && (
          <ReportComposerPanel
            config={pendingReportConfig}
            compact={compact}
            theme={theme}
            onChange={onReportConfigChange}
            onCancel={onReportConfigCancel}
            onGenerated={onReportConfigGenerated}
            requestHeaders={reportRequestHeaders}
          />
        )}
        <div style={{
          display: 'flex',
          gap: 10,
          alignItems: 'flex-end',
          maxWidth: 900,
          margin: '0 auto',
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={sourceUnavailableReason || '输入问题... (Enter 发送，Shift+Enter 换行)'}
            disabled={loading || disabled || Boolean(sourceUnavailableReason)}
            rows={compact ? 1 : 2}
            style={{
              flex: 1,
              padding: '10px 14px',
              border: '1px solid #e5e7eb',
              borderRadius: 8,
              fontSize: 14,
              resize: 'none',
              outline: 'none',
              fontFamily: 'inherit',
              backgroundColor: loading ? '#f9fafb' : '#fff',
            }}
          />
          {loading ? (
            <button
              onClick={onCancel}
              style={{
                padding: '10px 18px',
                border: '1px solid #fca5a5',
                borderRadius: 8,
                backgroundColor: '#fef2f2',
                color: '#dc2626',
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: 500,
                whiteSpace: 'nowrap',
              }}
            >
              取消
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!input.trim() || disabled || Boolean(sourceUnavailableReason)}
              style={{
                padding: '10px 18px',
                border: 'none',
                borderRadius: 8,
                backgroundColor: input.trim() && !disabled ? theme : '#d1d5db',
                color: '#fff',
                cursor: input.trim() && !disabled ? 'pointer' : 'not-allowed',
                fontSize: 13,
                fontWeight: 500,
                whiteSpace: 'nowrap',
                transition: 'all .15s',
              }}
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
