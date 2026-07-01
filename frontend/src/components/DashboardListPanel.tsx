import { useState, useMemo } from 'react';
import type { DashboardMeta } from '../types';

interface Props {
  dashboards: DashboardMeta[];
  currentDashboardId: string;
  onSwitch: (id: string) => void;
  onCreate: () => void;
}

const PANEL_BG = '#f8f9fb';
const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_BG = '#eef2ff';
const ACTIVE_TEXT = '#2563eb';

export function DashboardListPanel({ dashboards, currentDashboardId, onSwitch, onCreate }: Props) {
  const [search, setSearch] = useState('');

  /** 搜索过滤：只过滤列表展示，不修改数据 */
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return dashboards;
    return dashboards.filter(d => d.name.toLowerCase().includes(q));
  }, [dashboards, search]);

  return (
    <aside
      style={{
        width: 240,
        minWidth: 240,
        height: '100%',
        backgroundColor: PANEL_BG,
        borderRight: `1px solid ${BORDER}`,
        display: 'flex',
        flexDirection: 'column',
        userSelect: 'none',
        overflow: 'hidden',
      }}
    >
      {/* 标题 + 新建按钮 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 16px 12px',
        }}
      >
        <span
          style={{
            fontSize: 16,
            fontWeight: 500,
            color: TEXT_PRIMARY,
            lineHeight: '24px',
          }}
        >
          仪表板
        </span>
        <button
          onClick={onCreate}
          title="新建仪表板"
          aria-label="新建仪表板"
          style={{
            width: 28,
            height: 28,
            border: 'none',
            borderRadius: 6,
            backgroundColor: 'transparent',
            color: ACTIVE_TEXT,
            cursor: 'pointer',
            fontSize: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'background-color .12s',
          }}
          onMouseEnter={e => {
            e.currentTarget.style.backgroundColor = ACTIVE_BG;
          }}
          onMouseLeave={e => {
            e.currentTarget.style.backgroundColor = 'transparent';
          }}
        >
          +
        </button>
      </div>

      {/* 搜索框 */}
      <div style={{ padding: '0 16px 10px' }}>
        <div
          style={{
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke={TEXT_MUTED}
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ position: 'absolute', left: 8, pointerEvents: 'none' }}
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索仪表板…"
            style={{
              width: '100%',
              padding: '6px 8px 6px 28px',
              border: `1px solid ${BORDER}`,
              borderRadius: 6,
              fontSize: 12,
              color: TEXT_PRIMARY,
              backgroundColor: '#fff',
              outline: 'none',
              boxSizing: 'border-box',
            }}
            onFocus={e => {
              e.currentTarget.style.borderColor = ACTIVE_TEXT;
            }}
            onBlur={e => {
              e.currentTarget.style.borderColor = BORDER;
            }}
          />
        </div>
      </div>

      {/* 仪表板列表 */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 8px 8px',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        {filtered.length === 0 ? (
          <div
            style={{
              fontSize: 12,
              color: TEXT_MUTED,
              padding: '20px 12px',
              textAlign: 'center',
            }}
          >
            {search.trim() ? '无匹配结果' : '暂无仪表板'}
          </div>
        ) : (
          filtered.map(d => {
            const active = d.id === currentDashboardId;
            return (
              <div
                key={d.id}
                onClick={() => {
                  if (!active) onSwitch(d.id);
                }}
                title={d.name}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '8px 12px',
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: active ? 500 : 400,
                  color: active ? ACTIVE_TEXT : TEXT_SECONDARY,
                  backgroundColor: active ? ACTIVE_BG : 'transparent',
                  cursor: active ? 'default' : 'pointer',
                  transition: 'background-color .12s',
                  overflow: 'hidden',
                }}
                onMouseEnter={e => {
                  if (!active) e.currentTarget.style.backgroundColor = '#f3f4f6';
                }}
                onMouseLeave={e => {
                  if (!active) e.currentTarget.style.backgroundColor = 'transparent';
                }}
              >
                {/* 仪表板图标 */}
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke={active ? ACTIVE_TEXT : TEXT_MUTED}
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ flexShrink: 0, marginRight: 8 }}
                >
                  <rect x="3" y="3" width="7" height="9" rx="1" />
                  <rect x="14" y="3" width="7" height="5" rx="1" />
                  <rect x="14" y="12" width="7" height="9" rx="1" />
                  <rect x="3" y="16" width="7" height="5" rx="1" />
                </svg>
                <span
                  style={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {d.name}
                </span>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
