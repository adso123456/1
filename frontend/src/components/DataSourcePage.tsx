import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  formatDatabaseType,
  formatDataSourceStatus,
  sanitizeUserVisibleDataSourceText,
} from '../dataSourcePresentation';
import './DataSourcePage.css';

interface Source {
  source_id: string;
  display_name: string;
  description: string;
  database_type: 'mysql' | 'postgresql';
  status: string;
  enabled_for_chat: boolean;
  is_builtin: boolean;
  selected_tables_count: number;
  selected_columns_count: number;
  discovered_tables_count?: number;
  discovered_columns_count?: number;
  included_tables_count?: number;
  included_columns_count?: number;
  excluded_tables_count?: number;
  pending_confirmation_count?: number;
  host?: string;
  port?: number;
  database_name?: string;
  schema_name?: string;
  ssl_mode?: string;
  mysql_tls_mode?: string;
  ssl_ca_path?: string;
  ssl_cert_path?: string;
  ssl_key_path?: string;
  connect_timeout?: number;
  username?: string;
  has_password?: boolean;
  discovered_metadata?: MetadataColumn[];
  selected_scope?: MetadataColumn[];
}

interface MetadataColumn {
  schema: string;
  table: string;
  column: string;
  object_type: string;
  table_comment: string;
  type: string;
  comment: string;
  nullable: boolean;
  primary_key: boolean;
  ordinal_position: number;
}

const FILTER_STATUS = [
  'draft',
  'connected',
  'metadata_ready',
  'training_required',
  'ready',
  'disabled',
  'error',
];

const STATUS_CLASS: Record<string, string> = {
  ready: 'is-ready',
  disabled: 'is-disabled',
  error: 'is-error',
  training_required: 'is-warning',
  metadata_ready: 'is-progress',
  connected: 'is-progress',
  draft: 'is-muted',
};

function SearchIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-4-4" />
    </svg>
  );
}

function TableIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 10h18M9 4v16" />
    </svg>
  );
}

function DatabaseLogo({ type }: { type: Source['database_type'] }) {
  if (type === 'mysql') {
    return (
      <span className="data-source-logo data-source-logo--mysql" aria-label="MySQL">
        MY
      </span>
    );
  }
  return (
    <span className="data-source-logo data-source-logo--postgresql" aria-label="PostgreSQL">
      PG
    </span>
  );
}

function ChatToggleIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M20 15a3 3 0 0 1-3 3H9l-5 3v-6a3 3 0 0 1-1-2V7a3 3 0 0 1 3-3h11a3 3 0 0 1 3 3z" />
      <path d="M8 9h8M8 13h5" />
    </svg>
  );
}

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options?.body ? { 'Content-Type': 'application/json' } : {}),
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(sanitizeUserVisibleDataSourceText(
      payload?.detail || `请求失败（${response.status}）`,
    ));
  }
  return response.status === 204 ? undefined as T : response.json();
}

function SourceForm({
  source,
  onSaved,
  onCancel,
}: {
  source: Source | null;
  onSaved: (source: Source) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState({
    display_name: source?.display_name || '',
    description: source?.description || '',
    database_type: source?.database_type || 'postgresql',
    host: source?.host || '127.0.0.1',
    port: source?.port || 5433,
    database_name: source?.database_name || '',
    schema_name: source?.schema_name || 'public',
    ssl_mode: source?.ssl_mode || '',
    mysql_tls_mode: source?.mysql_tls_mode || 'disabled',
    ssl_ca_path: source?.ssl_ca_path || '',
    ssl_cert_path: source?.ssl_cert_path || '',
    ssl_key_path: source?.ssl_key_path || '',
    connect_timeout: source?.connect_timeout || 10,
    username: '',
    password: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const builtin = Boolean(source?.is_builtin);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (saving) return;
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [saving]);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const payload = source
        ? {
            display_name: form.display_name,
            description: form.description,
            ...(!builtin ? {
              host: form.host,
              port: Number(form.port),
              database_name: form.database_name,
              schema_name: form.schema_name,
              ...(form.database_type === 'postgresql' ? {
                ssl_mode: form.ssl_mode,
              } : {
                mysql_tls_mode: form.mysql_tls_mode,
                ssl_ca_path: form.ssl_ca_path,
                ssl_cert_path: form.ssl_cert_path,
                ssl_key_path: form.ssl_key_path,
              }),
              connect_timeout: Number(form.connect_timeout),
              ...(form.username ? { username: form.username } : {}),
              ...(form.password ? { password: form.password } : {}),
            } : {}),
          }
        : {
            ...form,
            port: Number(form.port),
            connect_timeout: Number(form.connect_timeout),
          };
      const saved = await api<Source>(
        source
          ? `/api/data-source-management/${encodeURIComponent(source.source_id)}`
          : '/api/data-source-management',
        { method: source ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
      );
      setForm(current => ({ ...current, password: '' }));
      onSaved(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="data-source-form">
      <h2 style={{ margin: '0 0 4px', fontSize: 17 }}>{source ? '编辑数据源' : '新建直连数据源'}</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 12 }}>
        <label>显示名称<input value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })} /></label>
        {!source && <label>数据库类型<select value={form.database_type} onChange={e => {
          const database_type = e.target.value as 'mysql' | 'postgresql';
          setForm({ ...form, database_type, port: database_type === 'mysql' ? 3306 : 5432, schema_name: database_type === 'mysql' ? '' : 'public' });
        }}><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option></select></label>}
        <label style={{ gridColumn: '1/-1' }}>描述<textarea value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
        {!builtin && <>
          <label>主机<input value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} /></label>
          <label>端口<input type="number" value={form.port} onChange={e => setForm({ ...form, port: Number(e.target.value) })} /></label>
          <label>数据库<input value={form.database_name} onChange={e => setForm({ ...form, database_name: e.target.value })} /></label>
          {form.database_type === 'postgresql' && <label>Schema<input value={form.schema_name} onChange={e => setForm({ ...form, schema_name: e.target.value })} /></label>}
          <label>用户名<input autoComplete="off" value={form.username} placeholder={source ? '留空保持原值' : ''} onChange={e => setForm({ ...form, username: e.target.value })} /></label>
          <label>密码<input type="password" autoComplete="new-password" value={form.password} placeholder={source ? '留空保持原密码' : ''} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
          {form.database_type === 'postgresql' ? (
            <label>PostgreSQL SSL 模式
              <select value={form.ssl_mode} onChange={e => setForm({ ...form, ssl_mode: e.target.value })}>
                <option value="">使用驱动默认值</option>
                <option value="disable">disable</option>
                <option value="prefer">prefer</option>
                <option value="require">require</option>
                <option value="verify-ca">verify-ca</option>
                <option value="verify-full">verify-full</option>
              </select>
            </label>
          ) : (
            <>
              <label>MySQL TLS 模式
                <select value={form.mysql_tls_mode} onChange={e => setForm({ ...form, mysql_tls_mode: e.target.value })}>
                  <option value="disabled">disabled</option>
                  <option value="required">required</option>
                  <option value="verify_ca">verify_ca</option>
                  <option value="verify_identity">verify_identity</option>
                </select>
              </label>
              {(form.mysql_tls_mode === 'verify_ca' || form.mysql_tls_mode === 'verify_identity') && (
                <label style={{ gridColumn: '1/-1' }}>CA 文件路径
                  <input value={form.ssl_ca_path} onChange={e => setForm({ ...form, ssl_ca_path: e.target.value })} />
                </label>
              )}
              {form.mysql_tls_mode !== 'disabled' && (
                <>
                  <label>客户端证书路径（可选）
                    <input value={form.ssl_cert_path} onChange={e => setForm({ ...form, ssl_cert_path: e.target.value })} />
                  </label>
                  <label>客户端私钥路径（可选）
                    <input value={form.ssl_key_path} onChange={e => setForm({ ...form, ssl_key_path: e.target.value })} />
                  </label>
                </>
              )}
            </>
          )}
          <label>连接超时（秒）<input type="number" value={form.connect_timeout} onChange={e => setForm({ ...form, connect_timeout: Number(e.target.value) })} /></label>
        </>}
      </div>
      {error && <p style={{ color: '#b91c1c', fontSize: 13 }}>{error}</p>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
        <button onClick={() => {
          if (window.confirm('确定离开编辑页？未保存的修改将丢失。')) onCancel();
        }}>取消</button>
        <button disabled={saving} onClick={save} style={{ background: '#2563eb', color: '#fff', borderColor: '#2563eb' }}>{saving ? '保存中…' : '保存'}</button>
      </div>
    </div>
  );
}

function ScopeSelector({
  source,
  onRefresh,
}: {
  source: Source;
  onRefresh: () => Promise<void>;
}) {
  const [detail, setDetail] = useState<Source>(source);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [tableSearch, setTableSearch] = useState('');

  const load = useCallback(async () => {
    const current = await api<Source>(`/api/data-source-management/${encodeURIComponent(source.source_id)}`);
    setDetail(current);
    setSelected(new Set((current.selected_scope || []).map(item => `${item.schema}.${item.table}.${item.column}`)));
  }, [source.source_id]);

  useEffect(() => { void load(); }, [load]);
  const grouped = useMemo(() => {
    const result = new Map<string, MetadataColumn[]>();
    for (const item of detail.discovered_metadata || []) {
      const key = `${item.schema}.${item.table}`;
      result.set(key, [...(result.get(key) || []), item]);
    }
    return result;
  }, [detail.discovered_metadata]);

  const action = async (name: string, run: () => Promise<unknown>) => {
    setBusy(name); setError('');
    try { await run(); await load(); await onRefresh(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '操作失败'); }
    finally { setBusy(''); }
  };

  return (
    <div className="data-source-scope">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button disabled={Boolean(busy)} onClick={() => action('test', () => api(`/api/data-source-management/${source.source_id}/test-connection`, { method: 'POST' }))}>{busy === 'test' ? '测试中…' : '测试连接'}</button>
        <button disabled={Boolean(busy)} onClick={() => action('discover', () => api(`/api/data-source-management/${source.source_id}/discover`, { method: 'POST' }))}>{busy === 'discover' ? '发现中…' : '读取表和字段'}</button>
        <button disabled={Boolean(busy) || selected.size === 0} onClick={() => action('scope', async () => {
          const byKey = new Map((detail.discovered_metadata || []).map(item => [`${item.schema}.${item.table}.${item.column}`, item]));
          await api(`/api/data-source-management/${source.source_id}/scope`, {
            method: 'PUT',
            body: JSON.stringify({ items: [...selected].map(key => byKey.get(key)) }),
          });
        })}>{busy === 'scope' ? '保存中…' : `保存范围（${selected.size} 字段）`}</button>
        <button disabled={Boolean(busy) || detail.selected_tables_count === 0} onClick={() => action('prepare', () => api(`/api/data-source-management/${source.source_id}/prepare`, { method: 'POST' }))}>{busy === 'prepare' ? '准备中…' : '生成问数资产'}</button>
      </div>
      <div className="data-source-scope-stats" aria-label="问数范围统计">
        <span>已发现<strong>{detail.discovered_tables_count ?? grouped.size}</strong>表</span>
        <span>已纳入问数<strong>{detail.included_tables_count ?? detail.selected_tables_count}</strong>表</span>
        <span>已排除<strong>{detail.excluded_tables_count ?? Math.max(grouped.size - detail.selected_tables_count, 0)}</strong>表</span>
        <span>待确认<strong>{detail.pending_confirmation_count ?? 0}</strong>表</span>
        <span>已纳入字段<strong>{detail.included_columns_count ?? detail.selected_columns_count}</strong>个</span>
      </div>
      {error && <p style={{ color: '#b91c1c', fontSize: 13 }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <input
          value={tableSearch}
          onChange={event => setTableSearch(event.target.value)}
          placeholder="搜索表名或表注释"
          style={{ flex: 1 }}
        />
        <button onClick={() => {
          const next = new Set(selected);
          [...grouped.entries()]
            .filter(([key, columns]) => (
              `${key} ${columns[0]?.table_comment || ''}`
                .toLowerCase()
                .includes(tableSearch.trim().toLowerCase())
            ))
            .forEach(([, columns]) => columns.forEach(column => {
              next.add(`${column.schema}.${column.table}.${column.column}`);
            }));
          setSelected(next);
        }}>全选当前筛选</button>
      </div>
      <div style={{ maxHeight: 360, overflow: 'auto', marginTop: 12 }}>
        {[...grouped.entries()].filter(([key, columns]) => (
          `${key} ${columns[0]?.table_comment || ''}`
            .toLowerCase()
            .includes(tableSearch.trim().toLowerCase())
        )).map(([tableKey, columns]) => {
          const keys = columns.map(item => `${item.schema}.${item.table}.${item.column}`);
          const all = keys.every(key => selected.has(key));
          return (
            <details key={tableKey} style={{ border: '1px solid #e5e7eb', borderRadius: 8, marginBottom: 8, padding: '9px 12px' }}>
              <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                <input type="checkbox" checked={all} onChange={event => {
                  event.stopPropagation();
                  setSelected(current => {
                    const next = new Set(current);
                    keys.forEach(key => event.target.checked ? next.add(key) : next.delete(key));
                    return next;
                  });
                }} /> {tableKey} <small style={{ color: '#6b7280' }}>· {columns.length} 字段 · {columns[0]?.object_type}</small>
              </summary>
              <div style={{ display: 'grid', gap: 5, marginTop: 8 }}>
                {columns.map(column => {
                  const key = `${column.schema}.${column.table}.${column.column}`;
                  return <label key={key} style={{ fontSize: 12, color: '#374151' }}>
                    <input type="checkbox" checked={selected.has(key)} onChange={event => setSelected(current => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(key);
                      else next.delete(key);
                      return next;
                    })} /> {column.column} · {column.type}{column.primary_key ? ' · 主键' : ''}{column.comment ? ` · ${column.comment}` : ''}
                  </label>;
                })}
              </div>
            </details>
          );
        })}
        {grouped.size === 0 && <p style={{ color: '#9ca3af', fontSize: 13 }}>请先测试连接并读取元数据。</p>}
      </div>
    </div>
  );
}

export function DataSourcePage({
  onDataSourcesChanged,
}: {
  onDataSourcesChanged: () => Promise<void>;
}) {
  const [sources, setSources] = useState<Source[]>([]);
  const [search, setSearch] = useState('');
  const [databaseType, setDatabaseType] = useState('');
  const [status, setStatus] = useState('');
  const [editing, setEditing] = useState<Source | null | 'new'>(null);
  const [expanded, setExpanded] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search) params.set('search', search);
      if (databaseType) params.set('database_type', databaseType);
      if (status) params.set('status', status);
      setSources(await api<Source[]>(`/api/data-source-management?${params}`));
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载失败');
    }
  }, [databaseType, search, status]);
  useEffect(() => { void load(); }, [load]);
  const refreshAll = useCallback(async () => {
    await Promise.all([load(), onDataSourcesChanged()]);
  }, [load, onDataSourcesChanged]);

  const openEditor = async (source: Source) => {
    setError('');
    try {
      setEditing(await api<Source>(
        `/api/data-source-management/${encodeURIComponent(source.source_id)}`,
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载数据源详情失败');
    }
  };

  const removeSource = async (source: Source) => {
    if (!window.confirm(`确认删除“${source.display_name}”？存在依赖时后端将拒绝。`)) return;
    setError('');
    try {
      await api(`/api/data-source-management/${encodeURIComponent(source.source_id)}`, {
        method: 'DELETE',
        body: JSON.stringify({
          confirmation: source.display_name,
          local_dependencies: [],
        }),
      });
      await refreshAll();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '删除失败');
    }
  };

  const setChatEnabled = async (source: Source, enabled: boolean) => {
    setError('');
    try {
      await api(
        `/api/data-source-management/${encodeURIComponent(source.source_id)}/${enabled ? 'enable' : 'disable'}`,
        { method: 'POST' },
      );
      await refreshAll();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '启停失败');
    }
  };

  if (editing !== null) {
    return <div className="data-source-page data-source-page--editing">
      <SourceForm source={editing === 'new' ? null : editing} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); void refreshAll(); }} />
    </div>;
  }

  return (
    <div className="data-source-page">
      <header className="data-source-header">
        <div className="data-source-heading">
          <h1>数据源</h1>
          <p>管理直连数据库、问数范围和独立运行资产</p>
        </div>
        <div className="data-source-toolbar">
          <label className="data-source-search">
            <SearchIcon />
            <input
              value={search}
              onChange={event => setSearch(event.target.value)}
              placeholder="搜索数据源"
              aria-label="搜索数据源"
            />
          </label>
          <select
            value={databaseType}
            onChange={event => setDatabaseType(event.target.value)}
            aria-label="筛选数据库类型"
          >
            <option value="">全部类型</option>
            <option value="postgresql">PostgreSQL</option>
            <option value="mysql">MySQL</option>
          </select>
          <select
            value={status}
            onChange={event => setStatus(event.target.value)}
            aria-label="筛选数据源状态"
          >
            <option value="">全部状态</option>
            {FILTER_STATUS.map(value => (
              <option key={value} value={value}>
                {formatDataSourceStatus(value, value === 'ready')}
              </option>
            ))}
          </select>
          <button className="data-source-new-button" onClick={() => setEditing('new')}>
            <span aria-hidden="true">＋</span>
            新建数据源
          </button>
        </div>
      </header>

      {error && (
        <div className="data-source-error" role="alert">
          <span>!</span>
          <p>{error}</p>
          <button onClick={() => void load()}>重新加载</button>
        </div>
      )}

      <div className="data-source-grid">
        {sources.map(source => (
          <article
            key={source.source_id}
            className={`data-source-card ${expanded === source.source_id ? 'is-expanded' : ''}`}
          >
            <div className="data-source-card-main">
              <div className="data-source-card-title">
                <DatabaseLogo type={source.database_type} />
                <div>
                  <div className="data-source-name-row">
                    <h2>{source.display_name}</h2>
                    {source.is_builtin && <span className="data-source-builtin">内置</span>}
                  </div>
                  <p className="data-source-database">
                    {formatDatabaseType(source.database_type)}
                  </p>
                </div>
              </div>
              <span className={`data-source-status ${STATUS_CLASS[source.status] || 'is-muted'}`}>
                {formatDataSourceStatus(source.status, source.enabled_for_chat)}
              </span>
            </div>

            <p className="data-source-description">
              {source.description || '暂无描述'}
            </p>

            <div className="data-source-card-footer">
              <div className="data-source-count" title="已选择的表和字段">
                <TableIcon />
                <span>{source.selected_tables_count} 表</span>
                <i />
                <span>{source.selected_columns_count} 字段</span>
              </div>
              <div className="data-source-card-actions">
                {source.status === 'ready' && (
                  <button
                    className="data-source-chat-button is-active"
                    onClick={() => void setChatEnabled(source, false)}
                    title="点击停用问数"
                  >
                    <ChatToggleIcon />
                    停用问数
                  </button>
                )}
                {source.status === 'disabled' && (
                  <button
                    className="data-source-chat-button"
                    onClick={() => void setChatEnabled(source, true)}
                  >
                    <ChatToggleIcon />
                    启用问数
                  </button>
                )}
                {source.status === 'draft' && (
                  <button className="data-source-action-button" onClick={() => void openEditor(source)}>
                    继续配置
                  </button>
                )}
                {source.status === 'error' && (
                  <button className="data-source-action-button is-danger" onClick={() => void openEditor(source)}>
                    检查配置
                  </button>
                )}
                {!['draft', 'error'].includes(source.status) && (
                  <button
                    className="data-source-action-button"
                    onClick={() => setExpanded(expanded === source.source_id ? '' : source.source_id)}
                  >
                    {expanded === source.source_id
                      ? '收起范围'
                      : source.status === 'connected'
                        ? '选择表和字段'
                        : source.status === 'metadata_ready'
                          ? '生成问数资产'
                          : source.status === 'training_required'
                            ? '刷新问数资产'
                            : '连接与范围'}
                  </button>
                )}
                <details className="data-source-menu">
                  <summary aria-label={`管理${source.display_name}`}>•••</summary>
                  <div>
                    <button onClick={() => void openEditor(source)}>编辑</button>
                    {!source.is_builtin && (
                      <button className="is-danger" onClick={() => void removeSource(source)}>
                        删除
                      </button>
                    )}
                  </div>
                </details>
              </div>
            </div>
            {expanded === source.source_id && <ScopeSelector source={source} onRefresh={refreshAll} />}
          </article>
        ))}
      </div>
      {!error && sources.length === 0 && (
        <div className="data-source-empty">
          <div><TableIcon /></div>
          <h2>没有找到数据源</h2>
          <p>调整筛选条件，或新建一个直连数据库。</p>
        </div>
      )}
    </div>
  );
}
