import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { AdminApiError, adminApi } from './adminApi';
import {
  DEFAULT_ASSISTANT_APPEARANCE,
  type AssistantAppearance,
} from './assistantAppearance';
import {
  AssistantAppearanceDialog,
} from './components/admin/AssistantAppearanceDialog';
import type {
  AdminDataSource,
  AssistantApplicationLink,
  AssistantApplicationView,
  CreateAssistantApplication,
} from './adminTypes';
import { formatDatabaseType } from './dataSourcePresentation';
import './AdminApp.css';

/* ── 表单与链接模型 ── */

interface FormState {
  appId: string;
  name: string;
  origins: string;
  sourceIds: string[];
  staleSourceIds: string[];
  showHistory: boolean;
  enabled: boolean;
  links: AssistantApplicationLink[];
}

interface ConfirmationState {
  action: 'disable' | 'delete';
  application: AssistantApplicationView;
}

interface RequestOwnership {
  controller: AbortController;
  epoch: number;
}

interface ActionOwnership {
  epoch: number;
  id: number;
}

interface AppearanceDialogState {
  epoch: number;
  sessionId: number;
  application: AssistantApplicationView;
}

const EMPTY_FORM: FormState = {
  appId: '',
  name: '',
  origins: '',
  sourceIds: [],
  staleSourceIds: [],
  showHistory: false,
  enabled: true,
  links: [],
};

function formFromApplication(
  application: AssistantApplicationView,
  knownSourceIds: Set<string>,
): FormState {
  return {
    appId: application.app_id,
    name: application.name,
    origins: application.allowed_origins.join('\n'),
    sourceIds: [...application.allowed_source_ids],
    staleSourceIds: application.allowed_source_ids.filter(
      sourceId => !knownSourceIds.has(sourceId),
    ),
    showHistory: application.show_history,
    enabled: application.enabled,
    links: application.application_links.map(link => ({ ...link })),
  };
}

function createLinkId(): string {
  const random = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `link-${random}`.slice(0, 64);
}

function newApplicationLink(sortOrder: number): AssistantApplicationLink {
  return {
    link_id: createLinkId(),
    name: '',
    url: '',
    open_mode: 'new_tab',
    enabled: true,
    sort_order: sortOrder,
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

function isSafeApplicationUrl(value: string): boolean {
  const normalized = value.trim();
  if (
    !normalized
    || /\s/.test(normalized)
    || normalized.includes('<')
    || normalized.includes('>')
    || normalized.includes('\\')
  ) return false;
  try {
    const decoded = decodeURIComponent(normalized);
    if (
      /\s/.test(decoded)
      || decoded.includes('<')
      || decoded.includes('>')
      || decoded.includes('\\')
    ) return false;
  } catch {
    return false;
  }
  try {
    const url = new URL(normalized);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && Boolean(url.hostname)
      && !url.username
      && !url.password
    );
  } catch {
    return false;
  }
}

function validateForm(
  form: FormState,
  editing: boolean,
  knownSourceIds: Set<string>,
): string | null {
  if (!editing && !/^[A-Za-z0-9_-]{3,64}$/.test(form.appId)) {
    return 'app_id 必须为 3～64 位字母、数字、下划线或短横线。';
  }
  if (!form.name.trim()) return '名称不能为空。';
  if (
    editing
    && form.sourceIds.some(sourceId => !knownSourceIds.has(sourceId))
  ) {
    return '请先取消已经失效的数据源授权，再保存修改。';
  }
  const invalidOrigin = normalizeOrigins(form.origins).find(
    origin => !isExactOrigin(origin),
  );
  if (invalidOrigin) {
    return 'Origin 必须是规范的精确 http/https Origin。';
  }
  for (const link of form.links) {
    if (!link.name.trim()) return '关联网站名称不能为空。';
    if (!link.url.trim() || !isSafeApplicationUrl(link.url.trim())) {
      return '请填写完整有效的关联网站 URL。';
    }
    if (!['new_tab', 'same_tab'].includes(link.open_mode)) {
      return '关联网站打开方式无效。';
    }
  }
  return null;
}

function commonPayload(form: FormState): Pick<
  CreateAssistantApplication,
  | 'name'
  | 'allowed_origins'
  | 'allowed_source_ids'
  | 'application_links'
  | 'show_history'
> {
  return {
    name: form.name.trim(),
    allowed_origins: normalizeOrigins(form.origins),
    allowed_source_ids: [...form.sourceIds],
    application_links: form.links.map((link, index) => ({
      ...link,
      name: link.name.trim(),
      url: link.url.trim(),
      sort_order: index,
    })),
    show_history: form.showHistory,
  };
}

function formatTimestamp(value: number): string {
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN');
}

/* ── 组件 ── */

interface AssistantManagementProps {
  embedded?: boolean;
}

export function AssistantManagement({
  embedded = false,
}: AssistantManagementProps) {
  const [dataSources, setDataSources] = useState<AdminDataSource[]>([]);
  const [applications, setApplications] = useState<
    AssistantApplicationView[]
  >([]);
  const [loading, setLoading] = useState(false);
  const [pageError, setPageError] = useState('');
  const [form, setForm] = useState<FormState | null>(null);
  const [editingAppId, setEditingAppId] = useState<string | null>(null);
  const [formError, setFormError] = useState('');
  const [confirmation, setConfirmation] =
    useState<ConfirmationState | null>(null);
  const [appearanceDialog, setAppearanceDialog] =
    useState<AppearanceDialogState | null>(null);
  const [appearanceError, setAppearanceError] = useState('');
  const [busyActions, setBusyActions] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);
  const lifecycleEpochRef = useRef(0);
  const actionIdRef = useRef(0);
  const formSessionRef = useRef(0);
  const formSubmitRef = useRef<number | null>(null);
  const formDialogOpenRef = useRef(false);
  const confirmationDialogOpenRef = useRef(false);
  const appearanceSessionRef = useRef(0);
  const appearanceDialogRef = useRef<AppearanceDialogState | null>(null);
  const appearanceSaveRef = useRef<{
    epoch: number;
    sessionId: number;
    request: RequestOwnership;
  } | null>(null);
  const controllersRef = useRef<Set<AbortController>>(new Set());
  const busyRef = useRef<Map<string, ActionOwnership>>(new Map());

  const abortRequests = useCallback(() => {
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
  }, []);

  useEffect(() => {
    const activeBusyActions = busyRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      lifecycleEpochRef.current += 1;
      abortRequests();
      activeBusyActions.clear();
    };
  }, [abortRequests]);

  const startRequest = useCallback((epoch: number): RequestOwnership => {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return { controller, epoch };
  }, []);

  const finishRequest = useCallback((request: RequestOwnership) => {
    controllersRef.current.delete(request.controller);
  }, []);

  const isCurrentRequest = useCallback((request: RequestOwnership) => (
    mountedRef.current && request.epoch === lifecycleEpochRef.current
  ), []);

  const beginAction = useCallback((
    key: string,
    epoch: number,
  ): ActionOwnership | null => {
    if (busyRef.current.has(key)) return null;
    const ownership = { epoch, id: actionIdRef.current + 1 };
    actionIdRef.current = ownership.id;
    busyRef.current.set(key, ownership);
    setBusyActions(new Set(busyRef.current.keys()));
    return ownership;
  }, []);

  const endAction = useCallback((
    key: string,
    ownership: ActionOwnership,
  ) => {
    const current = busyRef.current.get(key);
    if (
      !mountedRef.current
      || ownership.epoch !== lifecycleEpochRef.current
      || current?.id !== ownership.id
      || current.epoch !== ownership.epoch
    ) {
      return;
    }
    busyRef.current.delete(key);
    setBusyActions(new Set(busyRef.current.keys()));
  }, []);

  const handleError = useCallback((
    error: unknown,
    request: RequestOwnership,
  ) => {
    if (!isCurrentRequest(request)) return;
    if (error instanceof DOMException && error.name === 'AbortError') return;
    setPageError(
      error instanceof AdminApiError
        ? error.message
        : '管理服务暂时不可用。',
    );
  }, [isCurrentRequest]);

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

  const removeApplication = useCallback((appId: string) => {
    setApplications(current =>
      current.filter(item => item.app_id !== appId));
  }, []);

  const refresh = useCallback(async () => {
    const epoch = lifecycleEpochRef.current;
    const action = beginAction('refresh', epoch);
    if (!action) return;
    setLoading(true);
    setPageError('');
    const request = startRequest(epoch);
    try {
      const [sources, items] = await Promise.all([
        adminApi.listDataSources(request.controller.signal),
        adminApi.listApplications(request.controller.signal),
      ]);
      if (!isCurrentRequest(request)) return;
      setDataSources(sources);
      setApplications(items);
    } catch (error) {
      handleError(error, request);
    } finally {
      finishRequest(request);
      if (isCurrentRequest(request)) {
        setLoading(false);
        endAction('refresh', action);
      }
    }
  }, [
    beginAction,
    endAction,
    finishRequest,
    handleError,
    isCurrentRequest,
    startRequest,
  ]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openCreate = () => {
    if (
      formSubmitRef.current !== null
      || formDialogOpenRef.current
      || confirmationDialogOpenRef.current
      || appearanceDialogRef.current !== null
    ) return;
    formSessionRef.current += 1;
    formDialogOpenRef.current = true;
    setEditingAppId(null);
    setForm({ ...EMPTY_FORM, sourceIds: [], staleSourceIds: [] });
    setFormError('');
  };

  const openEdit = (application: AssistantApplicationView) => {
    if (
      formSubmitRef.current !== null
      || formDialogOpenRef.current
      || confirmationDialogOpenRef.current
      || appearanceDialogRef.current !== null
    ) return;
    formSessionRef.current += 1;
    formDialogOpenRef.current = true;
    const knownSourceIds = new Set(
      dataSources.map(source => source.source_id),
    );
    setEditingAppId(application.app_id);
    setForm(formFromApplication(application, knownSourceIds));
    setFormError('');
  };

  const closeForm = () => {
    if (formSubmitRef.current !== null) return;
    formSessionRef.current += 1;
    formDialogOpenRef.current = false;
    setForm(null);
    setEditingAppId(null);
    setFormError('');
  };

  const submitForm = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    const validationError = validateForm(
      form,
      editingAppId !== null,
      knownSourceIds,
    );
    if (validationError) {
      setFormError(validationError);
      return;
    }
    const actionKey = editingAppId ? `edit:${editingAppId}` : 'create';
    const epoch = lifecycleEpochRef.current;
    const action = beginAction(actionKey, epoch);
    if (!action) return;
    const formSession = formSessionRef.current;
    formSubmitRef.current = formSession;
    setFormError('');
    const request = startRequest(epoch);
    const ownsForm = () => (
      isCurrentRequest(request)
      && formSessionRef.current === formSession
      && formSubmitRef.current === formSession
    );
    try {
      if (editingAppId) {
        const updated = await adminApi.updateApplication(
          editingAppId,
          commonPayload(form),
          request.controller.signal,
        );
        if (!ownsForm()) return;
        replaceApplication(updated);
      } else {
        const payload: CreateAssistantApplication = {
          app_id: form.appId,
          enabled: form.enabled,
          ...DEFAULT_ASSISTANT_APPEARANCE,
          ...commonPayload(form),
        };
        const created = await adminApi.createApplication(
          payload,
          request.controller.signal,
        );
        if (!ownsForm()) return;
        replaceApplication(created);
      }
      if (!ownsForm()) return;
      formSubmitRef.current = null;
      closeForm();
    } catch (error) {
      if (!ownsForm()) return;
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setFormError(
          error instanceof AdminApiError
            ? error.message
            : '提交失败，请稍后重试。',
        );
      }
    } finally {
      finishRequest(request);
      if (ownsForm()) formSubmitRef.current = null;
      endAction(actionKey, action);
    }
  };

  const enableApplication = async (
    application: AssistantApplicationView,
  ) => {
    const actionKey = `enable:${application.app_id}`;
    const epoch = lifecycleEpochRef.current;
    const action = beginAction(actionKey, epoch);
    if (!action) return;
    const request = startRequest(epoch);
    try {
      const updated = await adminApi.enableApplication(
        application.app_id,
        request.controller.signal,
      );
      if (isCurrentRequest(request)) replaceApplication(updated);
    } catch (error) {
      handleError(error, request);
    } finally {
      finishRequest(request);
      endAction(actionKey, action);
    }
  };

  const openConfirmation = (
    action: ConfirmationState['action'],
    application: AssistantApplicationView,
  ) => {
    if (
      appearanceDialogRef.current !== null
      || formDialogOpenRef.current
      || confirmationDialogOpenRef.current
    ) return;
    confirmationDialogOpenRef.current = true;
    setConfirmation({ action, application });
  };

  const openAppearance = (application: AssistantApplicationView) => {
    if (
      appearanceDialogRef.current !== null
      || formDialogOpenRef.current
      || formSubmitRef.current !== null
      || confirmationDialogOpenRef.current
    ) return;
    appearanceSessionRef.current += 1;
    const dialog = {
      epoch: lifecycleEpochRef.current,
      sessionId: appearanceSessionRef.current,
      application,
    };
    appearanceDialogRef.current = dialog;
    setAppearanceDialog(dialog);
    setAppearanceError('');
  };

  const closeAppearance = () => {
    const dialog = appearanceDialogRef.current;
    const saving = appearanceSaveRef.current;
    if (
      saving
      && dialog
      && saving.epoch === dialog.epoch
      && saving.sessionId === dialog.sessionId
    ) return;
    appearanceSessionRef.current += 1;
    appearanceDialogRef.current = null;
    setAppearanceDialog(null);
    setAppearanceError('');
  };

  const saveAppearance = async (appearance: AssistantAppearance) => {
    const dialog = appearanceDialogRef.current;
    if (
      !dialog
      || appearanceSaveRef.current !== null
      || dialog.epoch !== lifecycleEpochRef.current
    ) return;
    const actionKey = `appearance:${dialog.application.app_id}`;
    const action = beginAction(actionKey, dialog.epoch);
    if (!action) return;
    const request = startRequest(dialog.epoch);
    const ownership = {
      epoch: dialog.epoch,
      sessionId: dialog.sessionId,
      request,
    };
    appearanceSaveRef.current = ownership;
    setAppearanceError('');
    const ownsAppearance = () => (
      isCurrentRequest(request)
      && appearanceSaveRef.current === ownership
      && appearanceDialogRef.current?.epoch === ownership.epoch
      && appearanceDialogRef.current.sessionId === ownership.sessionId
    );
    try {
      const updated = await adminApi.updateApplication(
        dialog.application.app_id,
        appearance,
        request.controller.signal,
      );
      if (!ownsAppearance()) return;
      replaceApplication(updated);
      appearanceSaveRef.current = null;
      appearanceSessionRef.current += 1;
      appearanceDialogRef.current = null;
      setAppearanceDialog(null);
    } catch (error) {
      if (!ownsAppearance()) return;
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setAppearanceError(
          error instanceof AdminApiError
            ? error.message
            : '保存外观失败，请稍后重试。',
        );
      }
    } finally {
      finishRequest(request);
      if (appearanceSaveRef.current === ownership) {
        appearanceSaveRef.current = null;
      }
      endAction(actionKey, action);
    }
  };

  const runConfirmedAction = async () => {
    if (!confirmation) return;
    const { action, application } = confirmation;
    const actionKey = `${action}:${application.app_id}`;
    const epoch = lifecycleEpochRef.current;
    const ownership = beginAction(actionKey, epoch);
    if (!ownership) return;
    confirmationDialogOpenRef.current = false;
    setConfirmation(null);
    const request = startRequest(epoch);
    try {
      if (action === 'disable') {
        const updated = await adminApi.disableApplication(
          application.app_id,
          request.controller.signal,
        );
        if (isCurrentRequest(request)) replaceApplication(updated);
      } else if (action === 'delete') {
        await adminApi.deleteApplication(
          application.app_id,
          request.controller.signal,
        );
        if (isCurrentRequest(request)) removeApplication(application.app_id);
      }
    } catch (error) {
      handleError(error, request);
    } finally {
      finishRequest(request);
      endAction(actionKey, ownership);
    }
  };

  const closeConfirmation = () => {
    confirmationDialogOpenRef.current = false;
    setConfirmation(null);
  };

  const knownSourceIds = new Set(
    dataSources.map(source => source.source_id),
  );
  const formStaleSourceIds = form
    ? [...new Set([
      ...form.staleSourceIds,
      ...form.sourceIds.filter(sourceId => !knownSourceIds.has(sourceId)),
    ])]
    : [];
  const formActionKey = form
    ? editingAppId ? `edit:${editingAppId}` : 'create'
    : null;
  const formSubmitting = formActionKey !== null
    && busyActions.has(formActionKey);

  return (
    <main
      className={`admin-shell${embedded ? ' admin-shell--embedded' : ''}`}
    >
      <header className="admin-header">
        <div>
          <p className="admin-eyebrow">ASSISTANT REGISTRY</p>
          <div className="admin-title-row">
            <h1>小助手管理</h1>
          </div>
          <p>管理嵌入应用的来源授权、展示配置、关联网站入口与启用状态。</p>
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
            disabled={
              formSubmitRef.current !== null
              || appearanceDialog !== null
            }
          >
            新建小助手
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
            <span>创建第一个嵌入应用，配置允许的来源和数据源。</span>
            <button
              className="admin-button admin-button--primary"
              type="button"
              onClick={openCreate}
              disabled={
                formSubmitRef.current !== null
                || appearanceDialog !== null
              }
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
                  <th>状态</th>
                  <th>授权范围</th>
                  <th>关联网站</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {applications.map(application => {
                  const enabledLinks = application.application_links.filter(
                    link => link.enabled,
                  );
                  const firstLink = enabledLinks[0];
                  return (
                  <tr key={application.app_id}>
                    <td data-label="应用">
                      <strong>{application.name}</strong>
                      <code className="admin-breakable">
                        {application.app_id}
                      </code>
                    </td>
                    <td data-label="状态">
                      <span className={
                        application.enabled
                          ? 'admin-status admin-status--enabled'
                          : 'admin-status admin-status--disabled'
                      }>
                        {application.enabled ? '已启用' : '已停用'}
                      </span>
                    </td>
                    <td data-label="授权范围">
                      <div className="admin-chip-group">
                        {application.allowed_source_ids.length
                          ? application.allowed_source_ids.map(sourceId => {
                            const source = dataSources.find(
                              item => item.source_id === sourceId,
                            );
                            return (
                            <span
                              className={
                                knownSourceIds.has(sourceId)
                                  ? 'admin-chip'
                                  : 'admin-chip admin-chip--stale'
                              }
                              key={sourceId}
                            >
                              <strong>{source?.display_name || '已失效的数据源'}</strong>
                              {!knownSourceIds.has(sourceId) && (
                                <small>已失效</small>
                              )}
                            </span>
                            );
                          })
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
                    <td data-label="关联网站">
                      <span className="admin-link-summary">
                        {application.application_links.length
                          ? `关联站点：${application.application_links[0].name} · ${application.application_links.length} 个入口`
                          : '配置网站入口'}
                      </span>
                    </td>
                    <td data-label="更新时间">
                      <span className="admin-muted">
                        {formatTimestamp(application.updated_at)}
                      </span>
                    </td>
                    <td data-label="操作">
                      <div className="admin-row-actions">
                        {enabledLinks.length === 0 ? (
                          <button
                            type="button"
                            onClick={() => openEdit(application)}
                          >
                            配置网站入口
                          </button>
                        ) : enabledLinks.length === 1 && firstLink ? (
                          <a
                            href={firstLink.url}
                            target={
                              firstLink.open_mode === 'new_tab'
                                ? '_blank'
                                : '_self'
                            }
                            rel={
                              firstLink.open_mode === 'new_tab'
                                ? 'noopener noreferrer'
                                : undefined
                            }
                          >
                            访问网站
                          </a>
                        ) : (
                          <details className="admin-action-menu">
                            <summary>访问网站</summary>
                            <div role="menu">
                              {enabledLinks.map(link => (
                                <a
                                  key={link.link_id}
                                  href={link.url}
                                  target={
                                    link.open_mode === 'new_tab'
                                      ? '_blank'
                                      : '_self'
                                  }
                                  rel={
                                    link.open_mode === 'new_tab'
                                      ? 'noopener noreferrer'
                                      : undefined
                                  }
                                  role="menuitem"
                                >
                                  {link.name}
                                </a>
                              ))}
                            </div>
                          </details>
                        )}
                        <button
                          type="button"
                          disabled={
                            formSubmitRef.current !== null
                            || appearanceDialog !== null
                          }
                          onClick={() => openEdit(application)}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          disabled={
                            appearanceDialog !== null
                            || form !== null
                            || confirmation !== null
                          }
                          onClick={() => openAppearance(application)}
                        >
                          外观
                        </button>
                        <details className="admin-action-menu">
                          <summary>更多</summary>
                          <div>
                            {application.enabled ? (
                              <button
                                type="button"
                                disabled={busyActions.has(
                                  `disable:${application.app_id}`,
                                )}
                                onClick={() => openConfirmation(
                                  'disable',
                                  application,
                                )}
                              >
                                停用
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
                              className="admin-danger-link"
                              disabled={busyActions.has(
                                `delete:${application.app_id}`,
                              )}
                              onClick={() => openConfirmation(
                                'delete',
                                application,
                              )}
                            >
                              删除
                            </button>
                          </div>
                        </details>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── 新建/编辑表单 ── */}
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
              <button
                type="button"
                onClick={closeForm}
                aria-label="关闭表单"
                disabled={formSubmitting}
              >
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
                  <small>仅接受规范的精确 http/https Origin，不包含路径或通配符。</small>
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
                        <strong>{source.display_name}</strong>
                        <small>{formatDatabaseType(source.database_type)}</small>
                      </span>
                    </label>
                  ))}
                </fieldset>
                {editingAppId && formStaleSourceIds.length > 0 && (
                  <fieldset className={
                    'admin-form-span-2 admin-source-fieldset '
                    + 'admin-source-fieldset--stale'
                  }>
                    <legend>已失效的数据源授权</legend>
                    {formStaleSourceIds.map(sourceId => (
                      <label key={sourceId}>
                        <input
                          type="checkbox"
                          checked={form.sourceIds.includes(sourceId)}
                          onChange={event => {
                            const sourceIds = event.target.checked
                              ? [...form.sourceIds, sourceId]
                              : form.sourceIds.filter(
                                selected => selected !== sourceId,
                              );
                            setForm({ ...form, sourceIds });
                          }}
                        />
                        <span>
                          <strong>已失效的数据源</strong>
                          <small>当前数据源配置中不存在</small>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                )}
                <fieldset className="admin-form-span-2 admin-link-fieldset">
                  <legend>关联网站入口</legend>
                  <div className="admin-link-fieldset__header">
                    <small>
                      仅用于管理页反向跳转，不参与 Origin 或数据源授权。
                    </small>
                    <button
                      type="button"
                      disabled={form.links.length >= 20 || formSubmitting}
                      onClick={() => setForm({
                        ...form,
                        links: [
                          ...form.links,
                          newApplicationLink(form.links.length),
                        ],
                      })}
                    >
                      添加入口
                    </button>
                  </div>
                  {form.links.length === 0 ? (
                    <p className="admin-link-empty">尚未配置关联网站入口。</p>
                  ) : (
                    <div className="admin-link-editor-list">
                      {form.links.map((link, index) => (
                        <div
                          className="admin-link-editor"
                          key={link.link_id}
                        >
                          <label>
                            <span>名称</span>
                            <input
                              value={link.name}
                              disabled={formSubmitting}
                              onChange={event => {
                                const links = form.links.map(item =>
                                  item.link_id === link.link_id
                                    ? { ...item, name: event.target.value }
                                    : item);
                                setForm({ ...form, links });
                              }}
                              placeholder="水务管理平台"
                            />
                          </label>
                          <label className="admin-link-editor__url">
                            <span>URL</span>
                            <input
                              type="url"
                              value={link.url}
                              disabled={formSubmitting}
                              onChange={event => {
                                const links = form.links.map(item =>
                                  item.link_id === link.link_id
                                    ? { ...item, url: event.target.value }
                                    : item);
                                setForm({ ...form, links });
                              }}
                              placeholder="https://example.com/path?tab=1#assistant"
                            />
                          </label>
                          <label>
                            <span>打开方式</span>
                            <select
                              value={link.open_mode}
                              disabled={formSubmitting}
                              onChange={event => {
                                const openMode = event.target.value as
                                  AssistantApplicationLink['open_mode'];
                                const links = form.links.map(item =>
                                  item.link_id === link.link_id
                                    ? { ...item, open_mode: openMode }
                                    : item);
                                setForm({ ...form, links });
                              }}
                            >
                              <option value="new_tab">新标签页</option>
                              <option value="same_tab">当前页面</option>
                            </select>
                          </label>
                          <label className="admin-link-editor__enabled">
                            <input
                              type="checkbox"
                              checked={link.enabled}
                              disabled={formSubmitting}
                              onChange={event => {
                                const links = form.links.map(item =>
                                  item.link_id === link.link_id
                                    ? { ...item, enabled: event.target.checked }
                                    : item);
                                setForm({ ...form, links });
                              }}
                            />
                            <span>启用</span>
                          </label>
                          <div className="admin-link-editor__actions">
                            <button
                              type="button"
                              aria-label={`上移入口 ${link.name || index + 1}`}
                              disabled={index === 0 || formSubmitting}
                              onClick={() => {
                                const links = [...form.links];
                                [links[index - 1], links[index]] = [
                                  links[index],
                                  links[index - 1],
                                ];
                                setForm({ ...form, links });
                              }}
                            >
                              ↑
                            </button>
                            <button
                              type="button"
                              aria-label={`下移入口 ${link.name || index + 1}`}
                              disabled={
                                index === form.links.length - 1
                                || formSubmitting
                              }
                              onClick={() => {
                                const links = [...form.links];
                                [links[index], links[index + 1]] = [
                                  links[index + 1],
                                  links[index],
                                ];
                                setForm({ ...form, links });
                              }}
                            >
                              ↓
                            </button>
                            <button
                              type="button"
                              disabled={formSubmitting}
                              onClick={() => setForm({
                                ...form,
                                links: form.links.filter(
                                  item => item.link_id !== link.link_id,
                                ),
                              })}
                            >
                              删除
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </fieldset>
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
                      仅控制当前嵌入页面内的会话切换，不代表服务端保存历史记录。
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
                      <small>禁用状态的应用无法使用嵌入功能。</small>
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
                  disabled={formSubmitting}
                >
                  取消
                </button>
                <button
                  className="admin-button admin-button--primary"
                  type="submit"
                  disabled={formSubmitting}
                >
                  {formSubmitting
                    ? '提交中…'
                    : editingAppId ? '保存修改' : '创建小助手'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {/* ── 外观设置 ── */}
      {appearanceDialog && (
        <AssistantAppearanceDialog
          key={`${appearanceDialog.epoch}:${appearanceDialog.sessionId}`}
          application={appearanceDialog.application}
          saving={busyActions.has(
            `appearance:${appearanceDialog.application.app_id}`,
          )}
          requestError={appearanceError}
          onClose={closeAppearance}
          onSave={saveAppearance}
        />
      )}

      {/* ── 确认对话框 ── */}
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
                ? '确认停用小助手？'
                : '确认删除小助手？'}
            </h2>
            <p>
              {confirmation.action === 'disable'
                ? '停用后，嵌入请求将被拒绝。'
                : '删除后不可恢复，该应用的所有配置将永久移除。'}
            </p>
            <code className="admin-breakable">
              {confirmation.application.app_id}
            </code>
            <div className="admin-modal-actions">
              <button
                className="admin-button"
                type="button"
                onClick={closeConfirmation}
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
    </main>
  );
}

export function AdminApp() {
  return <AssistantManagement />;
}
