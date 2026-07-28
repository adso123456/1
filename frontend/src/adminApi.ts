import type {
  AdminDataSource,
  AdminPreviewTokenResponse,
  AssistantApplicationSecretResponse,
  AssistantApplicationView,
  CreateAssistantApplication,
  UpdateAssistantApplication,
} from './adminTypes';

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: object;
  signal?: AbortSignal;
}

const ADMIN_PATH_PREFIX = '/api/admin/';

export class AdminApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'AdminApiError';
    this.status = status;
  }
}

function safeFallback(status: number): string {
  if (status === 400) return '提交内容不符合要求，请检查后重试。';
  if (status === 401) return '管理员 Token 无效。';
  if (status === 403) return '管理接口只允许本机且必须满足同源要求。';
  if (status === 404) return '管理接口未启用或当前服务未提供。';
  if (status === 409) return '相同 app_id 的小助手已存在。';
  if (status === 422) return '提交内容格式无效，请检查后重试。';
  return '管理服务暂时不可用。';
}

function readSafeDetail(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null;
  const detail = Reflect.get(value, 'detail');
  if (typeof detail !== 'string' || !detail.trim() || detail.length > 240) {
    return null;
  }
  if (
    /(authorization|bearer|token|secret|traceback|sqlite|sql|[a-z]:[\\/])/i
      .test(detail)
  ) {
    return null;
  }
  return detail;
}

async function request<T>(
  path: string,
  token: string,
  options: RequestOptions = {},
): Promise<T> {
  if (!path.startsWith(ADMIN_PATH_PREFIX)) {
    throw new AdminApiError(0, '管理请求路径无效。');
  }
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    Accept: 'application/json',
  };
  let body: string | undefined;
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? 'GET',
      body,
      signal: options.signal,
      credentials: 'omit',
      cache: 'no-store',
      headers,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    throw new AdminApiError(0, '无法连接管理服务。');
  }

  if (!response.ok) {
    let detail: string | null = null;
    try {
      detail = readSafeDetail(await response.json());
    } catch {
      detail = null;
    }
    throw new AdminApiError(
      response.status,
      detail ?? safeFallback(response.status),
    );
  }

  try {
    return await response.json() as T;
  } catch {
    throw new AdminApiError(0, '管理服务返回了无法识别的数据。');
  }
}

function applicationPath(appId: string): string {
  return `${ADMIN_PATH_PREFIX}assistant-applications/${encodeURIComponent(appId)}`;
}

export const adminApi = {
  listDataSources(token: string, signal?: AbortSignal) {
    return request<AdminDataSource[]>(
      `${ADMIN_PATH_PREFIX}data-sources`,
      token,
      { signal },
    );
  },
  listApplications(token: string, signal?: AbortSignal) {
    return request<AssistantApplicationView[]>(
      `${ADMIN_PATH_PREFIX}assistant-applications`,
      token,
      { signal },
    );
  },
  getApplication(token: string, appId: string, signal?: AbortSignal) {
    return request<AssistantApplicationView>(
      applicationPath(appId),
      token,
      { signal },
    );
  },
  createApplication(
    token: string,
    payload: CreateAssistantApplication,
    signal?: AbortSignal,
  ) {
    return request<AssistantApplicationSecretResponse>(
      `${ADMIN_PATH_PREFIX}assistant-applications`,
      token,
      { method: 'POST', body: payload, signal },
    );
  },
  updateApplication(
    token: string,
    appId: string,
    payload: UpdateAssistantApplication,
    signal?: AbortSignal,
  ) {
    return request<AssistantApplicationView>(
      applicationPath(appId),
      token,
      { method: 'PATCH', body: payload, signal },
    );
  },
  enableApplication(token: string, appId: string, signal?: AbortSignal) {
    return request<AssistantApplicationView>(
      `${applicationPath(appId)}/enable`,
      token,
      { method: 'POST', signal },
    );
  },
  disableApplication(token: string, appId: string, signal?: AbortSignal) {
    return request<AssistantApplicationView>(
      `${applicationPath(appId)}/disable`,
      token,
      { method: 'POST', signal },
    );
  },
  rotateSecret(token: string, appId: string, signal?: AbortSignal) {
    return request<AssistantApplicationSecretResponse>(
      `${applicationPath(appId)}/rotate-secret`,
      token,
      { method: 'POST', signal },
    );
  },
  issuePreviewToken(
    token: string,
    appId: string,
    signal?: AbortSignal,
  ) {
    return request<AdminPreviewTokenResponse>(
      `${applicationPath(appId)}/preview-token`,
      token,
      { method: 'POST', signal },
    );
  },
};
