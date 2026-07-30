import { useMemo, useState } from 'react';
import type { DataSourceSuggestion, DataSourceSummary } from '../types';
import {
  formatDatabaseType,
  safeDataSourceDisplayName,
  sanitizeUserVisibleDataSourceText,
} from '../dataSourcePresentation';

export function DataSourceSuggestionCard({
  suggestion,
  onOpen,
  dataSources = [],
}: {
  suggestion: DataSourceSuggestion;
  onOpen?: (
    sourceId: string,
    question: string,
  ) => Promise<boolean> | boolean;
  dataSources?: DataSourceSummary[];
}) {
  const [showChooser, setShowChooser] = useState(false);
  const [selectedSourceId, setSelectedSourceId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const available = useMemo(
    () => suggestion.suggestions.flatMap(candidate => {
      const latest = dataSources.find(
        source => source.source_id === candidate.source_id,
      );
      return latest?.status === 'ready' && latest.enabled_for_chat
        ? [latest]
        : [];
    }),
    [dataSources, suggestion.suggestions],
  );
  const currentName = safeDataSourceDisplayName(
    dataSources.find(
      source => source.source_id === suggestion.current_source_id,
    )?.display_name || suggestion.current_source_name,
  );
  const openSuggestion = async (sourceId: string) => {
    if (!onOpen || submitting) return;
    setSubmitting(true);
    setError('');
    const ok = await onOpen(sourceId, suggestion.original_question);
    setSubmitting(false);
    if (!ok) {
      setError('建议的数据源当前不可用，请重新选择可用数据源。');
      return;
    }
    setShowChooser(false);
  };

  return (
    <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', borderRadius: 9, padding: 14 }}>
      <div style={{ fontWeight: 600, color: '#1e3a8a', marginBottom: 5 }}>
        当前“{currentName}”不包含该问题所需的数据
      </div>
      <div style={{ color: '#475569', fontSize: 13, marginBottom: 12 }}>
        {sanitizeUserVisibleDataSourceText(suggestion.reason)}
      </div>
      {available.length === 1 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 12, background: '#fff', borderRadius: 7, padding: '10px 12px',
        }}>
          <span>
            <strong>{safeDataSourceDisplayName(available[0].display_name)}</strong>
            <small style={{ color: '#64748b', marginLeft: 7 }}>
              {formatDatabaseType(available[0].database_type)}
            </small>
          </span>
          <button
            disabled={submitting}
            onClick={() => void openSuggestion(available[0].source_id)}
            style={{
              border: 0, borderRadius: 6, background: '#2563eb', color: '#fff',
              padding: '7px 10px', cursor: 'pointer', fontSize: 12,
            }}
          >
            在该数据源中新建对话
          </button>
        </div>
      )}
      {available.length > 1 && (
        <button
          type="button"
          onClick={() => {
            setSelectedSourceId(available[0].source_id);
            setShowChooser(true);
          }}
          style={{
            border: 0, borderRadius: 6, background: '#2563eb', color: '#fff',
            padding: '8px 12px', cursor: 'pointer', fontSize: 12,
          }}
        >
          选择建议数据源
        </button>
      )}
      {available.length === 0 && (
        <div style={{ color: '#b45309', fontSize: 13 }}>
          建议的数据源当前不可用，请重新选择可用数据源。
        </div>
      )}
      {error && <div role="alert" style={{ color: '#b91c1c', fontSize: 13, marginTop: 8 }}>{error}</div>}
      {showChooser && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 1300,
          background: 'rgba(15,23,42,.38)', display: 'grid', placeItems: 'center',
          padding: 20,
        }}>
          <div role="dialog" aria-modal="true" aria-label="选择建议数据源" style={{
            width: 'min(520px, 100%)', maxHeight: '80vh', overflow: 'auto',
            background: '#fff', borderRadius: 12, padding: 20,
            boxShadow: '0 20px 50px rgba(0,0,0,.18)',
          }}>
            <h2 style={{ margin: '0 0 14px', fontSize: 17 }}>选择建议数据源</h2>
            <div style={{ display: 'grid', gap: 8 }}>
              {available.map(source => (
                <label key={source.source_id} style={{
                  display: 'flex', gap: 10, padding: 12,
                  border: '1px solid #e5e7eb', borderRadius: 8,
                }}>
                  <input
                    type="radio"
                    name="suggested-data-source"
                    checked={selectedSourceId === source.source_id}
                    onChange={() => setSelectedSourceId(source.source_id)}
                  />
                  <span>
                    <strong>{safeDataSourceDisplayName(source.display_name)}</strong>
                    <small style={{ color: '#64748b', marginLeft: 7 }}>
                      {formatDatabaseType(source.database_type)}
                    </small>
                  </span>
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
              <button type="button" disabled={submitting} onClick={() => setShowChooser(false)}>取消</button>
              <button
                type="button"
                disabled={!selectedSourceId || submitting}
                onClick={() => void openSuggestion(selectedSourceId)}
              >
                {submitting ? '创建中…' : '在该数据源中新建对话'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
