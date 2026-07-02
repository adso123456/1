import { useState, useRef, useEffect } from 'react';
import type { ChatMessage, ChartData, RenderableChartType } from '../types';
import { MessageBubble } from './MessageBubble';

interface Props {
  messages: ChatMessage[];
  loading: boolean;
  onSend: (text: string) => void;
  onCancel: () => void;
  onClear: () => void;
  onChangeChartType: (type: RenderableChartType) => void;
  /** 透传给 MessageBubble：点击"添加到仪表板" */
  onAddToDashboard?: (payload: { chart: ChartData; messageId: string; sql: string | null }) => void;
}

const SUGGESTIONS = [
  '夷陵区有哪些排污口？只列前5条',
  '统计各区县排污口数量，用图表展示',
  '查询2025年1月的监测数据，只取pH值有记录的前5条',
];

export function ChatArea({ messages, loading, onSend, onCancel, onClear, onChangeChartType, onAddToDashboard }: Props) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || loading) return;
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
      height: '100vh',
      backgroundColor: '#f5f5f5',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    }}>
      {/* 顶部栏 */}
      <header style={{
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
            {' '}
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
      </header>

      {/* 消息列表 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '20px 24px' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', paddingTop: 80 }}>
            <h2 style={{ fontSize: 18, color: '#374151', fontWeight: 500, marginBottom: 8 }}>
              有什么可以帮助你的？
            </h2>
            <p style={{ fontSize: 13, color: '#9ca3af', marginBottom: 24 }}>
              用中文自然语言提问，Agent 自动查询数据库并返回图表
            </p>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => { onSend(s); setInput(''); }}
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
                  onMouseEnter={e => (e.currentTarget.style.borderColor = '#2563eb')}
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
            onAddToDashboard={onAddToDashboard}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区 */}
      <div style={{
        padding: '16px 24px',
        backgroundColor: '#fff',
        borderTop: '1px solid #e5e7eb',
        flexShrink: 0,
      }}>
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
            placeholder="输入问题... (Enter 发送，Shift+Enter 换行)"
            disabled={loading}
            rows={2}
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
              disabled={!input.trim()}
              style={{
                padding: '10px 18px',
                border: 'none',
                borderRadius: 8,
                backgroundColor: input.trim() ? '#2563eb' : '#d1d5db',
                color: '#fff',
                cursor: input.trim() ? 'pointer' : 'not-allowed',
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
