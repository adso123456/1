import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { AdminApiError, adminApi } from './adminApi';
import type {
  AdminDataSource,
  AssistantApplicationSecretResponse,
  AssistantApplicationView,
  CreateAssistantApplication,
  UpdateAssistantApplication,
} from './adminTypes';
import './AdminApp.css';

interface FormState {
  appId: string;
  name: string;
  origins: string;
  sourceIds: string[];
  ttl: string;
  theme: string;
  logoUrl: string;
  welcome: string;
  welcomeDescription: string;
  showHistory: boolean;
  enabled: boolean;
}

interface SecretState {
  appId: string;
  value: string;
}

interface ConfirmationState {
  action: 'disable' | 'rotate';
  application: AssistantApplicationView;
}

const EMPTY_FORM: FormState = {
  appId: '',
  name: '',
  origins: '',
  sourceIds: [],
  ttl: '300',
  theme: '#1677ff',
  logoUrl: '',
  welcome: '有什么可以帮助你的？',
  welcomeDescription: '用中文自然语言提问，Agent 自动查询数据库并返回图表',
  showHistory: false,
  enabled: true,
};

function formFromApplication(
  application: AssistantApplicationView,
): FormState {
  return {
    appId: application.app_id,
    name: application.name,
    origins: application.allowed_origins.join('\n'),
    sourceIds: [...application.allowed_source_ids],
    ttl: String(application.token_ttl_seconds),
    theme: application.theme,
    logoUrl: application.logo_url,
    welcome: application.welcome,
    welcomeDescription: application.welcome_description,
    showHistory: application.show_history,
    enabled: application.enabled,
  };
}

function normalizeOrigins(value: string): string[] {
  const unique = new Set<string>();
  for (const line of value.split(/\r?\n/)) {
    const origin = line.trim();
    if (origin) unique.add(origin);
  }
  return [...unique];
}

function isExactOrigin(value: string): boolean {
  if (value.includes('*')) return false;
  try {
    const url = new URL(value);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && !url.username
      && !url.password
      && !url.search
      && !url.hash
      && url.pathname === '/'
      && url.origin === value
    );
  } catch {
    return false;
  }
}

function isValidLogoUrl(value: string): boolean {
  if (!value) return true;
  try {
    const url = new URL(value);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && !url.username
      && !url.password
    );
  } catch {
    return false;
  }
}

function validateForm(form: FormState, editing: boolean): string | null {
  if (!editing && !/^[A-Za-z0-9_-]{3,64}$/.test(form.appId)) {
    return 'app_id 必须为 3～64 位字母、数字、下划线或短横线。';
  }
  if (!form.name.trim()) return '名称不能为空。';
  const ttl = Number(form.ttl);
  if (!Number.isInteger(ttl) || ttl < 30 || ttl > 3600) {
    return 'Token 有效期必须是 30～3600 秒的整数。';
  }
  if (!/^#[0-9A-Fa-f]{6}$/.test(form.theme)) {
    return '主题色必须使用 #RRGGBB 格式。';
  }
  if (!isValidLogoUrl(form.logoUrl.trim())) {
    return 'Logo URL 必须为空或为不含凭据的 http/https URL。';
  }
  if (!form.welcome.trim()) return '欢迎语不能为空。';
  if (!form.welcomeDescription.trim()) return '欢迎描述不能为空。';
  const invalidOrigin = normalizeOrigins(form.origins).find(
    origin => !isExactOrigin(origin),
  );
  if (invalidOrigin) {
    return 'Origin 必须是规范的精确 http/https Origin，不能包含路径、参数、凭据或通配符。';
  }
  return null;
}

function commonPayload(form: FormState): UpdateAssistantApplication {
  return {
    name: form.name.trim(),
    allowed_origins: normalizeOrigins(form.origins),
    allowed_source_ids: [...form.sourceIds],
    token_ttl_seconds: Number(form.ttl),
    theme: form.theme.toLowerCase(),
    logo_url: form.logoUrl.trim(),
    welcome: form.welcome.trim(),
    welcome_description: form.welcomeDescription.trim(),
    show_history: form.showHistory,
  };
}

function formatTimestamp(value: number): string {
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN');
}

function viewFromSecretResponse(
  response: AssistantApplicationSecretResponse,
): AssistantApplicationView {
  const { app_secret: _secret, ...application } = response;
  return application;
}

export function AdminApp() {
  const [tokenInput, setTokenInput] = useState('');
  const [token, setToken] = useState('');
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState('');
  const [dataSources, setDataSources] = useState<AdminDataSource[]>([]);
  const [applications, setApplications] = useState<
    AssistantApplicationView[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [pageError, setPageError] = useState('');
  const [form, setForm] = useState<FormState | null>(null);
  const [editingAppId, setEditingAppId] = useState<string | null>(null);
  const [formError, setFormError] = useState('');
  const [secret, setSecret] = useState<SecretState | null>(null);
  const [copyStatus, setCopyStatus] = useState('');
  const [confirmation, setConfirmation] =
    useState<ConfirmationState | null>(null);
  const [busyActions, setBusyActions] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);
  const controllersRef = useRef<Set<AbortController>>(new Set());
  const busyRef = useRef<Set<string>>(new Set());

  const abortRequests = useCallback(() => {
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
  }, []);

  const lock = useCallback((message = '') => {
    abortRequests();
    setToken('');
    setTokenInput('');
    setDataSources([]);
    setApplications([]);
    setPageError('');
    setForm(null);
    setEditingAppId(null);
    setFormError('');
    setSecret(null);
    setCopyStatus('');
    setConfirmation(null);
    busyRef.current.clear();
    setBusyActions(new Set());
    setUnlockError(message);
  }, [abortRequests]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRequests();
    };
  }, [abortRequests]);

  const startRequest = useCallback(() => {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return controller;
  }, []);

  const finishRequest = useCallback((controller: AbortController) => {
    controllersRef.current.delete(controller);
  }, []);

  const beginAction = useCallback((key: string): boolean => {
    if (busyRef.current.has(key)) return false;
    busyRef.current.add(key);
    setBusyActions(new Set(busyRef.current));
    return true;
  }, []);

  const endAction = useCallback((key: string) => {
    busyRef.current.delete(key);
    if (mountedRef.current) setBusyActions(new Set(busyRef.current));
  }, []);

  const handleError = useCallback((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (error instanceof AdminApiError && error.status === 401) {
      lock('管理员 Token 无效。');
      return;
    }
    setPageError(
      error instanceof AdminApiError
        ? error.message
        : '管理服务暂时不可用。',
    );
  }, [lock]);

  const replaceApplication = useCallback(
    (next: AssistantApplicationView) => {
      setApplications(current => {
        const exists = current.some(item => item.app_id === next.app_id);
        const result = exists
          ? current.map(item => item.app_id === next.app_id ? next : item)
          : [...current, next];
        return result.sort((left, right) =>
          left.app_id.localeCompare(right.app_id));
      });
    },
    [],
  );

  const unlock = async (event: FormEvent) => {
    event.preventDefault();
    if (!tokenInput || !beginAction('unlock')) return;
    setUnlocking(true);
    setUnlockError('');
    const candidate = tokenInput;
    const controller = startRequest();
    try {
      const sources = await adminApi.listDataSources(
        candidate,
        controller.signal,
      );
      const items = await adminApi.listApplications(
        candidate,
        controller.signal,
      );
      if (!mountedRef.current) return;
      setToken(candidate);
      setTokenInput('');
      setDataSources(sources);
      setApplications(items);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setTokenInput('');
      setUnlockError(
        error instanceof AdminApiError
          ? error.message
          : '管理服务暂时不可用。',
      );
    } finally {
      finishRequest(controller);
      if (mountedRef.current) setUnlocking(false);
      endAction('unlock');
    }
  };

  const refresh = useCallback(async () => {
    if (!token || !beginAction('refresh')) return;
    setLoading(true);
    setPageError('');
    const controller = startRequest();
    try {
      const [sources, items] = await Promise.all([
        adminApi.listDataSources(token, controller.signal),
        adminApi.listApplications(token, controller.signal),
      ]);
      if (!mountedRef.current) return;
      setDataSources(sources);
      setApplications(items);
    } catch (error) {
      if (mountedRef.current) handleError(error);
    } finally {
      finishRequest(controller);
      if (mountedRef.current) setLoading(false);
      endAction('refresh');
    }
  }, [
    beginAction,
    endAction,
    finishRequest,
    handleError,
    startRequest,
    token,
  ]);

  const openCreate = () => {
    setEditingAppId(null);
    setForm({ ...EMPTY_FORM, sourceIds: [] });
    setFormError('');
  };

  const openEdit = (application: AssistantApplicationView) => {
    setEditingAppId(application.app_id);
    setForm(formFromApplication(application));
    setFormError('');
  };

  const closeForm = () => {
    setForm(null);
    setEditingAppId(null);
    setFormError('');
  };

  const submitForm = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    const validationError = validateForm(form, editingAppId !== null);
    if (validationError) {
      setFormError(validationError);
      return;
    }
    const actionKey = editingAppId ? `edit:${editingAppId}` : 'create';
    if (!beginAction(actionKey)) return;
    setFormError('');
    const controller = startRequest();
    try {
      if (editingAppId) {
        const updated = await adminApi.updateApplication(
          token,
          editingAppId,
          commonPayload(form),
          controller.signal,
        );
        if (!mountedRef.current) return;
        replaceApplication(updated);
      } else {
        const payload: CreateAssistantApplication = {
          app_id: form.appId,
          enabled: form.enabled,
          ...commonPayload(form),
        };
        const created = await adminApi.createApplication(
          token,
          payload,
          controller.signal,
        );
        if (!mountedRef.current) return;
        replaceApplication(viewFromSecretResponse(created));
        setSecret({ appId: created.app_id, value: created.app_secret });
      }
      closeForm();
    } catch (error) {
      if (error instanceof AdminApiError && error.status === 401) {
        handleError(error);
      } else if (
        !(error instanceof DOMException && error.name === 'AbortError')
      ) {
        setFormError(
          error instanceof AdminApiError
            ? error.message
            : '提交失败，请稍后重试。',
        );
      }
    } finally {
      finishRequest(controller);
      endAction(actionKey);
    }
  };

  const enableApplication = async (
    application: AssistantApplicationView,
  ) => {
    const actionKey = `enable:${application.app_id}`;
    if (!beginAction(actionKey)) return;
    const controller = startRequest();
    try {
      const updated = await adminApi.enableApplication(
        token,
        application.app_id,
        controller.signal,
      );
      if (mountedRef.current) replaceApplication(updated);
    } catch (error) {
      if (mountedRef.current) handleError(error);
    } finally {
      finishRequest(controller);
      endAction(actionKey);
    }
  };

  const runConfirmedAction = async () => {
    if (!confirmation) return;
    const { action, application } = confirmation;
    const actionKey = `${action}:${application.app_id}`;
    if (!beginAction(actionKey)) return;
    setConfirmation(null);
    const controller = startRequest();
    try {
      if (action === 'disable') {
        const updated = await adminApi.disableApplication(
          token,
          application.app_id,
          controller.signal,
        );
        if (mountedRef.current) replaceApplication(updated);
      } else {
        const rotated = await adminApi.rotateSecret(
          token,
          application.app_id,
          controller.signal,
        );
        if (!mountedRef.current) return;
        replaceApplication(viewFromSecretResponse(rotated));
        setSecret({
          appId: rotated.app_id,
          value: rotated.app_secret,
        });
      }
    } catch (error) {
      if (mountedRef.current) handleError(error);
    } finally {
      finishRequest(controller);
      endAction(actionKey);
    }
  };

  const closeSecret = () => {
    setSecret(null);
    setCopyStatus('');
  };

  const copySecret = async () => {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret.value);
      setCopyStatus('已复制到剪贴板。');
    } catch {
      setCopyStatus('复制失败，请手动复制。');
    }
  };

  if (!token) {
    return (
      <main className="admin-lock-shell">
        <section className="admin-lock-card" aria-labelledby="admin-lock-title">
          <div className="admin-brand-mark" aria-hidden="true">水</div>
          <p className="admin-eyebrow">LOCAL ADMINISTRATION</p>
          <h1 id="admin-lock-title">小助手管理</h1>
          <p className="admin-lock-intro">
            此页面仅供本机管理员使用。刷新或关闭页面后需要重新解锁。
          </p>
          <form onSubmit={unlock}>
            <label htmlFor="admin-token">管理员 Token</label>
            <input
              id="admin-token"
              type="password"
              autoComplete="off"
              value={tokenInput}
              onChange={event => setTokenInput(event.target.value)}
              disabled={unlocking}
              autoFocus
            />
            {unlockError && (
              <p className="admin-inline-error" role="alert">{unlockError}</p>
            )}
            <button
              className="admin-button admin-button--primary admin-button--wide"
              type="submit"
              disabled={unlocking || !tokenInput}
            >
              {unlocking ? '正在验证…' : '解锁管理页面'}
            </button>
          </form>
          <p className="admin-security-note">
            Token 仅保存在当前页面内存中，不会写入浏览器存储。
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="admin-shell">
      <header className="admin-header">
        <div>
          <p className="admin-eyebrow">ASSISTANT REGISTRY</p>
          <div className="admin-title-row">
            <h1>小助手管理</h1>
            <span className="admin-local-badge">仅限本机</span>
          </div>
          <p>管理嵌入应用的来源授权、展示配置与启用状态。</p>
        </div>
        <div className="admin-header-actions">
          <button
            className="admin-button"
            type="button"
            onClick={refresh}
            disabled={busyActions.has('refresh')}
          >
            {busyActions.has('refresh') ? '刷新中…' : '刷新'}
          </button>
          <button
            className="admin-button admin-button--primary"
            type="button"
            onClick={openCreate}
          >
            新建小助手
          </button>
          <button
            className="admin-button admin-button--danger-ghost"
            type="button"
            onClick={() => lock()}
          >
            锁定管理页面
          </button>
        </div>
      </header>

      <section className="admin-content">
        <div className="admin-section-heading">
          <div>
            <h2>小助手应用</h2>
            <p>共 {applications.length} 个应用</p>
          </div>
        </div>

        {pageError && (
          <div className="admin-error-banner" role="alert">
            <span>{pageError}</span>
            <button type="button" onClick={() => setPageError('')}>关闭</button>
          </div>
        )}

        {loading ? (
          <div className="admin-state-card">正在加载管理数据…</div>
        ) : applications.length === 0 ? (
          <div className="admin-state-card admin-state-card--empty">
            <strong>还没有小助手</strong>
            <span>创建第一个本机嵌入应用，配置允许的来源和数据源。</span>
            <button
              className="admin-button admin-button--primary"
              type="button"
              onClick={openCreate}
            >
              新建小助手
            </button>
          </div>
        ) : (
          <div className="admin-table-wrap">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>应用</th>
                  <th>状态与 Secret</th>
                  <th>授权范围</th>
                  <th>Token / 更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {applications.map(application => (
                  <tr key={application.app_id}>
                    <td data-label="应用">
                      <strong>{application.name}</strong>
                      <code className="admin-breakable">
                        {application.app_id}
                      </code>
                    </td>
                    <td data-label="状态与 Secret">
                      <span className={
                        application.enabled
                          ? 'admin-status admin-status--enabled'
                          : 'admin-status admin-status--disabled'
                      }>
                        {application.enabled ? '已启用' : '已禁用'}
                      </span>
                      <code className="admin-secret-mask">
                        {application.secret_mask}
                      </code>
                    </td>
                    <td data-label="授权范围">
                      <div className="admin-chip-group">
                        {application.allowed_source_ids.length
                          ? application.allowed_source_ids.map(sourceId => (
                            <span className="admin-chip" key={sourceId}>
                              {sourceId}
                            </span>
                          ))
                          : <span className="admin-muted">无授权数据源</span>}
                      </div>
                      <div className="admin-origin-list">
                        {application.allowed_origins.length
                          ? application.allowed_origins.map(origin => (
                            <code className="admin-breakable" key={origin}>
                              {origin}
                            </code>
                          ))
                          : <span className="admin-muted">无允许 Origin</span>}
                      </div>
                    </td>
                    <td data-label="Token / 更新时间">
                      <span>{application.token_ttl_seconds} 秒</span>
                      <span className="admin-muted">
                        {formatTimestamp(application.updated_at)}
                      </span>
                    </td>
                    <td data-label="操作">
                      <div className="admin-row-actions">
                        <button
                          type="button"
                          onClick={() => openEdit(application)}
                        >
                          编辑
                        </button>
                        {application.enabled ? (
                          <button
                            type="button"
                            disabled={busyActions.has(
                              `disable:${application.app_id}`,
                            )}
                            onClick={() => setConfirmation({
                              action: 'disable',
                              application,
                            })}
                          >
                            禁用
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={busyActions.has(
                              `enable:${application.app_id}`,
                            )}
                            onClick={() => enableApplication(application)}
                          >
                            {busyActions.has(`enable:${application.app_id}`)
                              ? '启用中…'
                              : '启用'}
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={busyActions.has(
                            `rotate:${application.app_id}`,
                          )}
                          onClick={() => setConfirmation({
                            action: 'rotate',
                            application,
                          })}
                        >
                          轮换 Secret
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {form && (
        <div className="admin-modal-backdrop" role="presentation">
          <section
            className="admin-modal admin-form-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-form-title"
          >
            <div className="admin-modal-header">
              <div>
                <p className="admin-eyebrow">
                  {editingAppId ? 'EDIT ASSISTANT' : 'NEW ASSISTANT'}
                </p>
                <h2 id="admin-form-title">
                  {editingAppId ? '编辑小助手' : '新建小助手'}
                </h2>
              </div>
              <button type="button" onClick={closeForm} aria-label="关闭表单">
                ×
              </button>
            </div>
            <form className="admin-form" onSubmit={submitForm}>
              <div className="admin-form-grid">
                <label>
                  <span>app_id</span>
                  <input
                    value={form.appId}
                    disabled={editingAppId !== null}
                    onChange={event => setForm({
                      ...form,
                      appId: event.target.value,
                    })}
                    placeholder="water-assistant"
                  />
                </label>
                <label>
                  <span>名称</span>
                  <input
                    value={form.name}
                    onChange={event => setForm({
                      ...form,
                      name: event.target.value,
                    })}
                  />
                </label>
                <label>
                  <span>Token 有效期（秒）</span>
                  <input
                    type="number"
                    min="30"
                    max="3600"
                    step="1"
                    value={form.ttl}
                    onChange={event => setForm({
                      ...form,
                      ttl: event.target.value,
                    })}
                  />
                </label>
                <label>
                  <span>主题色</span>
                  <div className="admin-color-field">
                    <input
                      type="color"
                      value={form.theme}
                      onChange={event => setForm({
                        ...form,
                        theme: event.target.value,
                      })}
                    />
                    <input
                      value={form.theme}
                      onChange={event => setForm({
                        ...form,
                        theme: event.target.value,
                      })}
                    />
                  </div>
                </label>
                <label className="admin-form-span-2">
                  <span>允许 Origin（一行一个）</span>
                  <textarea
                    rows={4}
                    value={form.origins}
                    onChange={event => setForm({
                      ...form,
                      origins: event.target.value,
                    })}
                    placeholder="https://example.com"
                  />
                  <small>仅接受规范的精确 http/https Origin。</small>
                </label>
                <fieldset className="admin-form-span-2 admin-source-fieldset">
                  <legend>授权数据源</legend>
                  {dataSources.map(source => (
                    <label key={source.source_id}>
                      <input
                        type="checkbox"
                        checked={form.sourceIds.includes(source.source_id)}
                        onChange={event => {
                          const sourceIds = event.target.checked
                            ? [...form.sourceIds, source.source_id]
                            : form.sourceIds.filter(
                              sourceId => sourceId !== source.source_id,
                            );
                          setForm({ ...form, sourceIds });
                        }}
                      />
                      <span>
                        <strong>{source.source_id}</strong>
                        <small>{source.database_type}</small>
                      </span>
                    </label>
                  ))}
                </fieldset>
                <label className="admin-form-span-2">
                  <span>Logo URL</span>
                  <input
                    value={form.logoUrl}
                    onChange={event => setForm({
                      ...form,
                      logoUrl: event.target.value,
                    })}
                    placeholder="https://example.com/logo.png"
                  />
                </label>
                <label className="admin-form-span-2">
                  <span>欢迎语</span>
                  <input
                    value={form.welcome}
                    onChange={event => setForm({
                      ...form,
                      welcome: event.target.value,
                    })}
                  />
                </label>
                <label className="admin-form-span-2">
                  <span>欢迎描述</span>
                  <textarea
                    rows={3}
                    value={form.welcomeDescription}
                    onChange={event => setForm({
                      ...form,
                      welcomeDescription: event.target.value,
                    })}
                  />
                </label>
                <label className="admin-checkbox-row admin-form-span-2">
                  <input
                    type="checkbox"
                    checked={form.showHistory}
                    onChange={event => setForm({
                      ...form,
                      showHistory: event.target.checked,
                    })}
                  />
                  <span>
                    <strong>显示当前嵌入生命周期内的会话切换</strong>
                    <small>
                      仅控制当前嵌入页面内的会话切换，不代表服务端保存历史记录，也不会跨设备同步。
                    </small>
                  </span>
                </label>
                {!editingAppId && (
                  <label className="admin-checkbox-row admin-form-span-2">
                    <input
                      type="checkbox"
                      checked={form.enabled}
                      onChange={event => setForm({
                        ...form,
                        enabled: event.target.checked,
                      })}
                    />
                    <span>
                      <strong>创建后立即启用</strong>
                      <small>禁用状态的应用不能签发或使用嵌入 Token。</small>
                    </span>
                  </label>
                )}
              </div>
              {formError && (
                <p className="admin-inline-error" role="alert">{formError}</p>
              )}
              <div className="admin-modal-actions">
                <button
                  className="admin-button"
                  type="button"
                  onClick={closeForm}
                >
                  取消
                </button>
                <button
                  className="admin-button admin-button--primary"
                  type="submit"
                  disabled={busyActions.has(
                    editingAppId ? `edit:${editingAppId}` : 'create',
                  )}
                >
                  {busyActions.has(
                    editingAppId ? `edit:${editingAppId}` : 'create',
                  )
                    ? '提交中…'
                    : editingAppId ? '保存修改' : '创建小助手'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {confirmation && (
        <div className="admin-modal-backdrop" role="presentation">
          <section
            className="admin-modal admin-confirm-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="admin-confirm-title"
          >
            <p className="admin-eyebrow">CONFIRM ACTION</p>
            <h2 id="admin-confirm-title">
              {confirmation.action === 'disable'
                ? '确认禁用小助手？'
                : '确认轮换 Secret？'}
            </h2>
            <p>
              {confirmation.action === 'disable'
                ? '禁用后，现有嵌入 Token 将立即失效。'
                : '轮换后，旧 Secret 和使用旧 Secret 签发的 Token 将立即失效。'}
            </p>
            <code className="admin-breakable">
              {confirmation.application.app_id}
            </code>
            <div className="admin-modal-actions">
              <button
                className="admin-button"
                type="button"
                onClick={() => setConfirmation(null)}
              >
                取消
              </button>
              <button
                className="admin-button admin-button--danger"
                type="button"
                onClick={runConfirmedAction}
              >
                确认
              </button>
            </div>
          </section>
        </div>
      )}

      {secret && (
        <div className="admin-modal-backdrop" role="presentation">
          <section
            className="admin-modal admin-secret-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="admin-secret-title"
          >
            <p className="admin-eyebrow">ONE-TIME SECRET</p>
            <h2 id="admin-secret-title">请立即保存应用 Secret</h2>
            <p className="admin-secret-warning">
              关闭后无法再次查看。请将它安全交给对应宿主应用。
            </p>
            <label>
              <span>{secret.appId}</span>
              <textarea value={secret.value} readOnly rows={3} />
            </label>
            {copyStatus && <p className="admin-copy-status">{copyStatus}</p>}
            <div className="admin-modal-actions">
              <button
                className="admin-button"
                type="button"
                onClick={copySecret}
              >
                复制
              </button>
              <button
                className="admin-button admin-button--primary"
                type="button"
                onClick={closeSecret}
              >
                我已保存，关闭
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
