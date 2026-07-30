import { useMemo, useState } from 'react';
import type { DataSourceSummary } from '../types';
import {
  formatDatabaseType,
  formatDataSourceStatus,
} from '../dataSourcePresentation';

interface Props {
  sources: DataSourceSummary[];
  onConfirm: (sourceId: string) => Promise<boolean> | boolean;
  onClose: () => void;
}

export function NewConversationSourceDialog({
  sources,
  onConfirm,
  onClose,
}: Props) {
  const [search, setSearch] = useState('');
  const [databaseType, setDatabaseType] = useState('');
  const [selected, setSelected] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const filtered = useMemo(
    () => sources.filter(source => (
      (!databaseType || source.database_type === databaseType)
      && (
        !search.trim()
        || `${source.display_name} ${source.description}`
          .toLowerCase()
          .includes(search.trim().toLowerCase())
      )
    )),
    [databaseType, search, sources],
  );

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1200,
      background: 'rgba(15,23,42,.38)', display: 'grid', placeItems: 'center',
      padding: 20,
    }}>
      <div role="dialog" aria-modal="true" aria-label="选择数据源" style={{
        width: 'min(680px, 100%)', maxHeight: '82vh', overflow: 'auto',
        background: '#fff', borderRadius: 14, boxShadow: '0 20px 50px rgba(0,0,0,.18)',
        padding: 24,
      }}>
        <h2 style={{ margin: 0, fontSize: 19, color: '#111827' }}>选择数据源</h2>
        <p style={{ color: '#6b7280', fontSize: 13, margin: '6px 0 18px' }}>
          对话创建后将永久绑定，不能在当前对话中切换。
        </p>
        <div style={{ display: 'flex', gap: 8, marginBottom: 14 }}>
          <input
            aria-label="搜索数据源"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder="搜索名称或描述"
            style={{ flex: 1, padding: '9px 11px', border: '1px solid #d1d5db', borderRadius: 7 }}
          />
          <select
            aria-label="数据库类型"
            value={databaseType}
            onChange={event => setDatabaseType(event.target.value)}
            style={{ padding: '9px 11px', border: '1px solid #d1d5db', borderRadius: 7 }}
          >
            <option value="">全部类型</option>
            <option value="postgresql">PostgreSQL</option>
            <option value="mysql">MySQL</option>
          </select>
        </div>
        <div style={{ display: 'grid', gap: 10 }}>
          {filtered.map(source => (
            <label key={source.source_id} style={{
              display: 'flex', gap: 12, padding: 14,
              border: `1px solid ${selected === source.source_id ? '#2563eb' : '#e5e7eb'}`,
              borderRadius: 9, cursor: 'pointer',
              background: selected === source.source_id ? '#eff6ff' : '#fff',
            }}>
              <input
                type="radio"
                name="new-conversation-source"
                checked={selected === source.source_id}
                onChange={() => setSelected(source.source_id)}
              />
              <span style={{ flex: 1 }}>
                <strong style={{ display: 'block', color: '#1f2937', fontSize: 14 }}>
                  {source.display_name}
                </strong>
                <span style={{ color: '#6b7280', fontSize: 12 }}>
                  {formatDatabaseType(source.database_type)}
                  {' · '}
                  {formatDataSourceStatus(source.status, source.enabled_for_chat)}
                  {' · '}
                  {source.selected_tables_count} 张表
                </span>
                {source.description && (
                  <span style={{ display: 'block', color: '#9ca3af', fontSize: 12, marginTop: 3 }}>
                    {source.description}
                  </span>
                )}
              </span>
            </label>
          ))}
          {filtered.length === 0 && (
            <div style={{ padding: 28, textAlign: 'center', color: '#9ca3af' }}>
              没有匹配的可用数据源
            </div>
          )}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 20 }}>
          <button onClick={onClose} disabled={submitting} style={{
            padding: '8px 16px', border: '1px solid #d1d5db', borderRadius: 7,
            background: '#fff', cursor: 'pointer',
          }}>取消</button>
          <button
            disabled={!selected || submitting}
            onClick={async () => {
              setSubmitting(true);
              const ok = await onConfirm(selected);
              setSubmitting(false);
              if (ok) onClose();
            }}
            style={{
              padding: '8px 16px', border: 0, borderRadius: 7,
              color: '#fff', background: !selected ? '#93c5fd' : '#2563eb',
              cursor: !selected || submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? '创建中…' : '开始对话'}
          </button>
        </div>
      </div>
    </div>
  );
}
