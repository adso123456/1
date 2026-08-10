export type ApplicationMode =
  | 'workspace'
  | 'widget'
  | 'embed-demo'
  | 'admin';
export type WidgetAccessMode =
  | 'protected'
  | 'local-development'
  | 'invalid';

const LOCAL_DEVELOPMENT_WIDGET_MARKER = 'project-embed-demo';

export function resolveApplicationMode(
  pathname: string,
  search: string,
): ApplicationMode {
  if (pathname === '/embed-demo') return 'embed-demo';
  const params = new URLSearchParams(search);
  const mode = params.get('mode');
  if (mode === 'widget') return 'widget';
  if (mode === 'admin') return 'admin';
  return 'workspace';
}

export function buildWidgetUrl(
  agentUrl: string,
  parentOrigin?: string,
  instanceId?: string,
  localDevelopment = false,
): string {
  const url = new URL('/?mode=widget', agentUrl);
  if (parentOrigin) url.searchParams.set('parentOrigin', parentOrigin);
  if (instanceId) url.searchParams.set('instanceId', instanceId);
  if (localDevelopment) {
    url.searchParams.set('devWidget', LOCAL_DEVELOPMENT_WIDGET_MARKER);
  }
  return url.toString();
}

export function resolveWidgetAccessMode(
  urlValue: string,
  agentOrigin: string,
  developmentBuild: boolean,
): WidgetAccessMode {
  const url = new URL(urlValue);
  const parentOrigin = url.searchParams.get('parentOrigin') || '';
  const instanceId = url.searchParams.get('instanceId')?.trim() || '';
  let normalizedParentOrigin = '';
  try {
    const candidate = new URL(parentOrigin);
    normalizedParentOrigin = candidate.origin === parentOrigin
      ? candidate.origin
      : '';
  } catch {
    return 'invalid';
  }
  if (
    !normalizedParentOrigin
    || !/^[A-Za-z0-9_-]{1,128}$/.test(instanceId)
  ) {
    return 'invalid';
  }
  if (normalizedParentOrigin !== agentOrigin) return 'protected';
  if (
    developmentBuild
    && url.searchParams.get('devWidget') === LOCAL_DEVELOPMENT_WIDGET_MARKER
  ) {
    return 'local-development';
  }
  // 同源生产嵌入（如演示页由应用自身提供）同样走父页面桥接，
  // 否则 WidgetApp 会进入 invalid 分支，iframe 不发 ready 导致加载超时。
  return 'protected';
}

export function buildWorkspaceUrl(
  agentUrl: string,
  sessionId?: string,
  storageNamespace?: string,
): string {
  const url = new URL('/', agentUrl);
  if (sessionId) url.searchParams.set('session', sessionId);
  if (storageNamespace) {
    url.searchParams.set('embed_scope', storageNamespace);
  }
  return url.toString();
}

export function readWorkspaceSessionId(urlValue: string): string {
  const value = new URL(urlValue).searchParams.get('session')?.trim() || '';
  return /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : '';
}

export function readWorkspaceStorageNamespace(urlValue: string): string {
  const value = new URL(urlValue).searchParams.get('embed_scope')?.trim() || '';
  return value.length <= 512 && !/[\u0000-\u001f\u007f]/.test(value)
    ? value
    : '';
}

export function clearWorkspaceSessionParam(urlValue: string): string {
  const url = new URL(urlValue);
  url.searchParams.delete('session');
  return `${url.pathname}${url.search}${url.hash}`;
}
