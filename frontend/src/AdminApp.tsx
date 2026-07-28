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
  AssistantApplicationSecretResponse,
  AssistantApplicationView,
  CreateAssistantApplication,
} from './adminTypes';
import './AdminApp.css';

interface FormState {
  appId: string;
  name: string;
  origins: string;
  sourceIds: string[];
  staleSourceIds: string[];
  ttl: string;
  showHistory: boolean;
  enabled: boolean;
}

interface SecretState {
  id: number;
  epoch: number;
  appId: string;
  value: string;
}

interface SecretOwnership {
  epoch: number;
  id: number;
  status: 'pending' | 'displaying';
  operation: 'create' | 'rotate';
  appId: string | null;
}

interface ConfirmationState {
  action: 'disable' | 'rotate';
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
  ttl: '300',
  showHistory: false,
  enabled: true,
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
    ttl: String(application.token_ttl_seconds),
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

function validateForm(
  form: FormState,
  editing: boolean,
  knownSourceIds: Set<string>,
): string | null {
  if (!editing && !/^[A-Za-z0-9_-]{3,64}$/.test(form.appId)) {
    return 'app_id 必须为 3～64 位字母、数字、下划线或短横线。';
  }
  if (!form.name.trim()) return '名称不能为空。';
  const ttl = Number(form.ttl);
  if (!Number.isInteger(ttl) || ttl < 30 || ttl > 3600) {
    return 'Token 有效期必须是 30～3600 秒的整数。';
  }
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
    return 'Origin 必须是规范的精确 http/https Origin，不能包含路径、参数、凭据或通配符。';
  }
  return null;
}

function commonPayload(form: FormState): Pick<
  CreateAssistantApplication,
  | 'name'
  | 'allowed_origins'
  | 'allowed_source_ids'
  | 'token_ttl_seconds'
  | 'show_history'
> {
  return {
    name: form.name.trim(),
    allowed_origins: normalizeOrigins(form.origins),
    allowed_source_ids: [...form.sourceIds],
    token_ttl_seconds: Number(form.ttl),
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
  const [secret, setSecret] = useState<SecretState | null>(null);
  const [secretOwnership, setSecretOwnership] =
    useState<SecretOwnership | null>(null);
  const [copyStatus, setCopyStatus] = useState('');
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
  const secretOperationIdRef = useRef(0);
  const secretOwnershipRef = useRef<SecretOwnership | null>(null);
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

  const acquireSecretOwnership = useCallback((
    operation: SecretOwnership['operation'],
    appId: string | null,
  ): SecretOwnership | null => {
    if (secretOwnershipRef.current !== null) return null;
    secretOperationIdRef.current += 1;
    const ownership: SecretOwnership = {
      epoch: lifecycleEpochRef.current,
      id: secretOperationIdRef.current,
      status: 'pending',
      operation,
      appId,
    };
    secretOwnershipRef.current = ownership;
    setSecretOwnership(ownership);
    return ownership;
  }, []);

  const ownsSecretFlow = useCallback((
    ownership: Pick<SecretOwnership, 'epoch' | 'id'>,
  ) => {
    const current = secretOwnershipRef.current;
    return (
      mountedRef.current
      && ownership.epoch === lifecycleEpochRef.current
      && current?.epoch === ownership.epoch
      && current.id === ownership.id
    );
  }, []);

  const markSecretDisplaying = useCallback((
    ownership: SecretOwnership,
  ): SecretOwnership | null => {
    if (!ownsSecretFlow(ownership)) return null;
    const displaying = { ...ownership, status: 'displaying' as const };
    secretOwnershipRef.current = displaying;
    setSecretOwnership(displaying);
    return displaying;
  }, [ownsSecretFlow]);

  const releaseSecretOwnership = useCallback((
    ownership: Pick<SecretOwnership, 'epoch' | 'id'>,
  ) => {
    const current = secretOwnershipRef.current;
    if (
      current?.epoch !== ownership.epoch
      || current.id !== ownership.id
    ) {
      return;
    }
    secretOwnershipRef.current = null;
    if (mountedRef.current) setSecretOwnership(null);
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
      || secretOwnershipRef.current !== null
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
      || secretOwnershipRef.current !== null
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
    const secretOwner = editingAppId === null
      ? acquireSecretOwnership('create', form.appId)
      : null;
    if (editingAppId === null && !secretOwner) {
      setFormError('已有一次性 Secret 操作正在进行，请完成后再试。');
      return;
    }
    const action = beginAction(actionKey, epoch);
    if (!action) {
      if (secretOwner) releaseSecretOwnership(secretOwner);
      return;
    }
    const formSession = formSessionRef.current;
    formSubmitRef.current = formSession;
    let secretDelivered = false;
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
        if (
          !ownsForm()
          || !secretOwner
          || !ownsSecretFlow(secretOwner)
        ) return;
        replaceApplication(viewFromSecretResponse(created));
        const displayingOwner = markSecretDisplaying(secretOwner);
        if (!displayingOwner) return;
        setSecret({
          id: displayingOwner.id,
          epoch: displayingOwner.epoch,
          appId: created.app_id,
          value: created.app_secret,
        });
        secretDelivered = true;
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
      if (secretOwner && !secretDelivered) {
        releaseSecretOwnership(secretOwner);
      }
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
      || secretOwnershipRef.current !== null
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
      || secretOwnershipRef.current !== null
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
    const secretOwner = action === 'rotate'
      ? acquireSecretOwnership('rotate', application.app_id)
      : null;
    if (action === 'rotate' && !secretOwner) {
      confirmationDialogOpenRef.current = false;
      setConfirmation(null);
      setPageError('已有一次性 Secret 操作正在进行，请完成后再试。');
      return;
    }
    const ownership = beginAction(actionKey, epoch);
    if (!ownership) {
      if (secretOwner) releaseSecretOwnership(secretOwner);
      return;
    }
    let secretDelivered = false;
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
      } else {
        const rotated = await adminApi.rotateSecret(
          application.app_id,
          request.controller.signal,
        );
        if (
          !isCurrentRequest(request)
          || !secretOwner
          || !ownsSecretFlow(secretOwner)
        ) return;
        replaceApplication(viewFromSecretResponse(rotated));
        const displayingOwner = markSecretDisplaying(secretOwner);
        if (!displayingOwner) return;
        setSecret({
          id: displayingOwner.id,
          epoch: displayingOwner.epoch,
          appId: rotated.app_id,
          value: rotated.app_secret,
        });
        secretDelivered = true;
      }
    } catch (error) {
      handleError(error, request);
    } finally {
      finishRequest(request);
      if (secretOwner && !secretDelivered) {
        releaseSecretOwnership(secretOwner);
      }
      endAction(actionKey, ownership);
    }
  };

  const closeConfirmation = () => {
    confirmationDialogOpenRef.current = false;
    setConfirmation(null);
  };

  const closeSecret = () => {
    if (secret) {
      releaseSecretOwnership(secret);
    }
    setSecret(null);
    setCopyStatus('');
  };

  const copySecret = async () => {
    if (!secret) return;
    const { epoch, id } = secret;
    try {
      await navigator.clipboard.writeText(secret.value);
      if (!ownsSecretFlow({ epoch, id })) return;
      setCopyStatus('已复制到剪贴板。');
    } catch {
      const current = secretOwnershipRef.current;
      if (
        epoch !== lifecycleEpochRef.current
        || current?.epoch !== epoch
        || current.id !== id
      ) return;
      setCopyStatus('复制失败，请手动复制。');
    }
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
  const secretFlowActive = secretOwnership !== null;

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
            disabled={
              formSubmitRef.current !== null
              || secretFlowActive
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
            <span>创建第一个本机嵌入应用，配置允许的来源和数据源。</span>
            <button
              className="admin-button admin-button--primary"
              type="button"
              onClick={openCreate}
              disabled={
                formSubmitRef.current !== null
                || secretFlowActive
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
                            <span
                              className={
                                knownSourceIds.has(sourceId)
                                  ? 'admin-chip'
                                  : 'admin-chip admin-chip--stale'
                              }
                              key={sourceId}
                            >
                              <strong>{sourceId}</strong>
                              {!knownSourceIds.has(sourceId) && (
                                <small>已失效</small>
                              )}
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
                            || secretFlowActive
                          }
                          onClick={() => openAppearance(application)}
                        >
                          外观设置
                        </button>
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
                          disabled={
                            secretFlowActive
                            || appearanceDialog !== null
                            || busyActions.has(
                              `rotate:${application.app_id}`,
                            )
                          }
                          onClick={() => openConfirmation(
                            'rotate',
                            application,
                          )}
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
                          <strong>{sourceId}</strong>
                          <small>当前数据源配置中不存在</small>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                )}
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

export function AdminApp() {
  return <AssistantManagement />;
}
