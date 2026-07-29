import type { DataSourceSuggestion, DataSourceSummary } from '../types';

export function DataSourceSuggestionCard({
  suggestion,
  onOpen,
  dataSources = [],
}: {
  suggestion: DataSourceSuggestion;
  onOpen?: (sourceId: string, question: string) => void;
  dataSources?: DataSourceSummary[];
}) {
  return (
    <div style={{ border: '1px solid #bfdbfe', background: '#eff6ff', borderRadius: 9, padding: 14 }}>
      <div style={{ fontWeight: 600, color: '#1e3a8a', marginBottom: 5 }}>
        当前“{suggestion.current_source_name}”中没有找到该问题所需的数据
      </div>
      <div style={{ color: '#475569', fontSize: 13, marginBottom: 12 }}>
        {suggestion.reason}
      </div>
      {suggestion.suggestions.map(source => {
        const latest = dataSources.find(
          item => item.source_id === source.source_id,
        );
        return (
        <div key={source.source_id} style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 12, background: '#fff', borderRadius: 7, padding: '10px 12px',
        }}>
          <span>
            <strong>{latest?.display_name || source.display_name}</strong>
            <small style={{ color: '#64748b', marginLeft: 7 }}>
              {(latest?.database_type || source.database_type).toUpperCase()}
            </small>
          </span>
          <button
            onClick={() => onOpen?.(source.source_id, suggestion.original_question)}
            style={{
              border: 0, borderRadius: 6, background: '#2563eb', color: '#fff',
              padding: '7px 10px', cursor: 'pointer', fontSize: 12,
            }}
          >
            在该数据源中新建对话
          </button>
        </div>
        );
      })}
    </div>
  );
}
