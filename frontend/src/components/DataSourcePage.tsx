import { useCallback, useEffect, useMemo, useState } from 'react';

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

const STATUS_LABEL: Record<string, string> = {
  draft: '待配置',
  connected: '已连接',
  metadata_ready: '范围已保存',
  training_required: '需要刷新资产',
  ready: '可问数',
  disabled: '已停用',
  error: '错误',
};

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
    throw new Error(payload?.detail || `请求失败（${response.status}）`);
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
    <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 20 }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 17 }}>{source ? '编辑数据源' : '新建直连数据源'}</h2>
      {source && <p style={{ margin: '0 0 16px', color: '#6b7280', fontSize: 12 }}>内部 ID：{source.source_id}（不可修改）</p>}
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
    <div style={{ marginTop: 16, borderTop: '1px solid #e5e7eb', paddingTop: 16 }}>
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

  if (editing !== null) {
    return <div className="data-source-page" style={{ padding: 24, overflow: 'auto', height: '100%', background: '#f5f7fb' }}>
      <SourceForm source={editing === 'new' ? null : editing} onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); void refreshAll(); }} />
    </div>;
  }

  return (
    <div className="data-source-page" style={{ padding: 24, overflow: 'auto', height: '100%', background: '#f5f7fb', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 18 }}>
        <div><h1 style={{ margin: 0, fontSize: 21 }}>数据源</h1><p style={{ color: '#6b7280', fontSize: 13, margin: '5px 0 0' }}>管理直连数据库、问数范围和独立运行资产</p></div>
        <button onClick={() => setEditing('new')} style={{ background: '#2563eb', color: '#fff', borderColor: '#2563eb' }}>＋ 新建数据源</button>
      </div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 15 }}>
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索名称或描述" />
        <select value={databaseType} onChange={e => setDatabaseType(e.target.value)}><option value="">全部类型</option><option value="postgresql">PostgreSQL</option><option value="mysql">MySQL</option></select>
        <select value={status} onChange={e => setStatus(e.target.value)}><option value="">全部状态</option>{Object.entries(STATUS_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      </div>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      <div style={{ display: 'grid', gap: 12 }}>
        {sources.map(source => (
          <div key={source.source_id} style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, padding: 18 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
              <div>
                <h2 style={{ margin: 0, fontSize: 16 }}>{source.display_name} {source.is_builtin && <small style={{ color: '#2563eb' }}>内置</small>}</h2>
                <p style={{ color: '#6b7280', fontSize: 13, margin: '5px 0' }}>{source.description || '暂无描述'}</p>
                <small style={{ color: '#9ca3af' }}>{source.database_type.toUpperCase()} · {STATUS_LABEL[source.status] || source.status} · {source.selected_tables_count} 表 / {source.selected_columns_count} 字段</small>
              </div>
              <div style={{ display: 'flex', gap: 7, alignItems: 'start', flexWrap: 'wrap', justifyContent: 'end' }}>
                <button onClick={async () => setEditing(await api<Source>(`/api/data-source-management/${source.source_id}`))}>编辑</button>
                <button onClick={() => setExpanded(expanded === source.source_id ? '' : source.source_id)}>{expanded === source.source_id ? '收起范围' : '连接与范围'}</button>
                <button onClick={async () => { await api(`/api/data-source-management/${source.source_id}/${source.status === 'disabled' ? 'enable' : 'disable'}`, { method: 'POST' }); await refreshAll(); }}>{source.status === 'disabled' ? '启用' : '停用'}</button>
                {!source.is_builtin && <button onClick={async () => {
                  if (!window.confirm(`确认删除“${source.display_name}”？存在依赖时后端将拒绝。`)) return;
                  try {
                    await api(`/api/data-source-management/${source.source_id}`, { method: 'DELETE', body: JSON.stringify({ confirmation: source.display_name, local_dependencies: [] }) });
                    await refreshAll();
                  } catch (caught) { setError(caught instanceof Error ? caught.message : '删除失败'); }
                }} style={{ color: '#b91c1c' }}>删除</button>}
              </div>
            </div>
            {expanded === source.source_id && <ScopeSelector source={source} onRefresh={refreshAll} />}
          </div>
        ))}
      </div>
    </div>
  );
}
