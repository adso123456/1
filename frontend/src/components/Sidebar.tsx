import type { DataSourceSummary, SessionMeta } from '../types';

interface Props {
  sessions: SessionMeta[];
  currentSessionId: string;
  loading: boolean;
  currentView: 'chat' | 'dashboard';
  onViewChange: (view: 'chat' | 'dashboard') => void;
  onNewSession: () => void;
  onSwitchSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  dataSources: DataSourceSummary[];
  currentSourceId: string;
  sourceLocked: boolean;
  dataSourceError: string | null;
  onSelectDataSource: (sourceId: string) => boolean;
}

/* ---- 内联 SVG 图标 ---- */

function ChatIcon({ color }: { color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

function DatabaseIcon({ color }: { color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  );
}

function DashboardIcon({ color }: { color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1" />
      <rect x="14" y="3" width="7" height="5" rx="1" />
      <rect x="14" y="12" width="7" height="9" rx="1" />
      <rect x="3" y="16" width="7" height="5" rx="1" />
    </svg>
  );
}

function SettingsIcon({ color }: { color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

/* ---- 共享样式值 ---- */

const NAV_BG = '#f8f9fb';
const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_BG = '#eef2ff';
const ACTIVE_TEXT = '#2563eb';

export function Sidebar({ sessions, currentSessionId, loading, currentView, onViewChange, onNewSession, onSwitchSession, onDeleteSession, dataSources, currentSourceId, sourceLocked, dataSourceError, onSelectDataSource }: Props) {
  const disabled = loading;

  return (
    <aside style={{
      width: 232,
      minWidth: 232,
      height: '100%',
      backgroundColor: NAV_BG,
      borderRight: `1px solid ${BORDER}`,
      display: 'flex',
      flexDirection: 'column',
      userSelect: 'none',
      overflow: 'hidden',
    }}>
      {/* ---- 品牌区域 ---- */}
      <div style={{
        padding: '20px 12px 16px',
        margin: '0 12px',
        borderBottom: `1px solid ${BORDER}`,
      }}>
        <div style={{
          fontSize: 15,
          fontWeight: 600,
          color: TEXT_PRIMARY,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <span style={{
            display: 'inline-flex',
            width: 28,
            height: 28,
            borderRadius: 6,
            backgroundColor: '#2563eb',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
              <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
            </svg>
          </span>
          水利智能问答
        </div>
      </div>

      {/* ---- 当前会话数据源 ---- */}
      <div style={{ padding: '12px 12px 0' }}>
        <label
          htmlFor="data-source-select"
          style={{ display: 'block', fontSize: 11, color: TEXT_MUTED, marginBottom: 5 }}
        >
          数据源
        </label>
        <select
          id="data-source-select"
          value={currentSourceId}
          disabled={disabled || sourceLocked || dataSources.length === 0}
          onChange={event => onSelectDataSource(event.target.value)}
          title={sourceLocked ? '已有消息的会话不能切换数据源，请先新建会话。' : '选择当前会话的数据源'}
          style={{
            width: '100%',
            padding: '7px 8px',
            border: `1px solid ${BORDER}`,
            borderRadius: 6,
            backgroundColor: disabled || sourceLocked ? '#f3f4f6' : '#fff',
            color: TEXT_PRIMARY,
            fontSize: 12,
          }}
        >
          {dataSources.length !== 1 && <option value="">请选择数据源</option>}
          {dataSources.map(source => (
            <option key={source.source_id} value={source.source_id}>
              {source.source_id} ({source.database_type})
            </option>
          ))}
        </select>
        {sourceLocked && (
          <div style={{ color: TEXT_MUTED, fontSize: 10, marginTop: 4 }}>
            已绑定；切换数据源请新建会话
          </div>
        )}
        {dataSourceError && (
          <div style={{ color: '#b91c1c', fontSize: 10, marginTop: 4 }}>
            {dataSourceError}
          </div>
        )}
      </div>

      {/* ---- 新对话按钮 ---- */}
      <div style={{ padding: '12px 12px 8px' }}>
        <button
          onClick={onNewSession}
          disabled={disabled}
          title={disabled ? '正在生成回答，请等待完成' : '创建新对话'}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 6,
            width: '100%',
            padding: '8px 0',
            border: `1px solid ${BORDER}`,
            borderRadius: 6,
            backgroundColor: disabled ? '#f3f4f6' : '#fff',
            color: disabled ? TEXT_MUTED : TEXT_PRIMARY,
            cursor: disabled ? 'not-allowed' : 'pointer',
            fontSize: 13,
            fontWeight: 500,
            transition: 'all .12s',
          }}
          onMouseEnter={e => { if (!disabled) { e.currentTarget.style.borderColor = ACTIVE_TEXT; e.currentTarget.style.color = ACTIVE_TEXT; } }}
          onMouseLeave={e => { if (!disabled) { e.currentTarget.style.borderColor = BORDER; e.currentTarget.style.color = TEXT_PRIMARY; } }}
        >
          <PlusIcon />
          新对话
        </button>
      </div>

      {/* ---- 会话历史 ---- */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '0 12px 4px',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
      }}>
        <div style={{
          fontSize: 11,
          fontWeight: 500,
          color: TEXT_MUTED,
          padding: '6px 12px 4px',
          letterSpacing: '0.5px',
          flexShrink: 0,
        }}>
          会话历史
        </div>

        {sessions.length === 0 && (
          <div style={{
            fontSize: 12,
            color: TEXT_MUTED,
            padding: '12px 12px',
            textAlign: 'center',
          }}>
            暂无历史会话
          </div>
        )}

        {sessions.map(s => {
          const active = s.id === currentSessionId;
          return (
            <div
              key={s.id}
              onClick={() => { if (!disabled && !active) onSwitchSession(s.id); }}
              title={s.title}
              style={{
                display: 'flex',
                alignItems: 'center',
                padding: '4px 8px 4px 12px',
                borderRadius: 6,
                fontSize: 13,
                fontWeight: active ? 500 : 400,
                color: active ? ACTIVE_TEXT : TEXT_SECONDARY,
                backgroundColor: active ? ACTIVE_BG : 'transparent',
                cursor: disabled ? 'not-allowed' : active ? 'default' : 'pointer',
                opacity: disabled ? 0.5 : 1,
                transition: 'background-color .12s',
              }}
              onMouseEnter={e => { if (!active && !disabled) e.currentTarget.style.backgroundColor = '#f3f4f6'; }}
              onMouseLeave={e => { if (!active && !disabled) e.currentTarget.style.backgroundColor = 'transparent'; }}
            >
              <span style={{
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {s.title}
              </span>
              <button
                onClick={e => {
                  e.stopPropagation();
                  if (window.confirm(`确定删除会话「${s.title}」吗？`)) {
                    onDeleteSession(s.id);
                  }
                }}
                disabled={disabled}
                title="删除会话"
                aria-label={`删除会话「${s.title}」`}
                style={{
                  flexShrink: 0,
                  width: 24,
                  height: 24,
                  marginLeft: 4,
                  border: 'none',
                  borderRadius: 4,
                  backgroundColor: 'transparent',
                  color: TEXT_MUTED,
                  cursor: disabled ? 'not-allowed' : 'pointer',
                  fontSize: 16,
                  lineHeight: '24px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  opacity: disabled ? 0.3 : 0.5,
                  transition: 'opacity .12s, background-color .12s, color .12s',
                }}
                onMouseEnter={e => {
                  if (!disabled) {
                    e.currentTarget.style.backgroundColor = '#fee2e2';
                    e.currentTarget.style.color = '#dc2626';
                    e.currentTarget.style.opacity = '1';
                  }
                }}
                onMouseLeave={e => {
                  if (!disabled) {
                    e.currentTarget.style.backgroundColor = 'transparent';
                    e.currentTarget.style.color = TEXT_MUTED;
                    e.currentTarget.style.opacity = '0.5';
                  }
                }}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      {/* ---- 导航（静态占位） ---- */}
      <div style={{
        borderTop: `1px solid ${BORDER}`,
        padding: '8px 12px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 2,
      }}>
        <div style={{
          fontSize: 11,
          fontWeight: 500,
          color: TEXT_MUTED,
          padding: '4px 12px 4px',
          letterSpacing: '0.5px',
        }}>
          导航
        </div>

        {[
          { key: 'chat' as const, label: '智能问答', Icon: ChatIcon },
          { key: 'datasource' as const, label: '数据源', Icon: DatabaseIcon },
          { key: 'dashboard' as const, label: '仪表板', Icon: DashboardIcon },
          { key: 'settings' as const, label: '设置', Icon: SettingsIcon },
        ].map(({ key, label, Icon }) => {
          const navigable = key === 'chat' || key === 'dashboard';
          const active = key === currentView;
          const iconColor = active ? ACTIVE_TEXT : TEXT_MUTED;

          return (
            <div
              key={key}
              onClick={() => { if (navigable) onViewChange(key); }}
              title={navigable ? `切换到${label}` : label}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 12px',
                borderRadius: 6,
                fontSize: 13,
                fontWeight: active ? 500 : 400,
                color: active ? ACTIVE_TEXT : TEXT_SECONDARY,
                backgroundColor: active ? ACTIVE_BG : 'transparent',
                cursor: navigable ? 'pointer' : 'default',
                transition: 'background-color .12s',
              }}
              onMouseEnter={e => { if (navigable && !active) e.currentTarget.style.backgroundColor = '#f3f4f6'; }}
              onMouseLeave={e => { if (navigable && !active) e.currentTarget.style.backgroundColor = 'transparent'; }}
            >
              <Icon color={iconColor} />
              {label}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
