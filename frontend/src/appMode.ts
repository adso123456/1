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
export const ADMIN_PREVIEW_WIDGET_MARKER =
  'project-admin-preview-v1';

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

export function buildAdminPreviewWidgetUrl(
  agentUrl: string,
  parentOrigin: string,
  instanceId: string,
): string {
  const url = new URL(
    buildWidgetUrl(agentUrl, parentOrigin, instanceId),
  );
  url.searchParams.set(
    'adminPreview',
    ADMIN_PREVIEW_WIDGET_MARKER,
  );
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
    url.searchParams.get('adminPreview')
    === ADMIN_PREVIEW_WIDGET_MARKER
  ) {
    return 'protected';
  }
  return (
    developmentBuild
    && url.searchParams.get('devWidget') === LOCAL_DEVELOPMENT_WIDGET_MARKER
  )
    ? 'local-development'
    : 'invalid';
}

export function buildWorkspaceUrl(
  agentUrl: string,
  sessionId?: string,
): string {
  const url = new URL('/', agentUrl);
  if (sessionId) url.searchParams.set('session', sessionId);
  return url.toString();
}

export function readWorkspaceSessionId(urlValue: string): string {
  const value = new URL(urlValue).searchParams.get('session')?.trim() || '';
  return /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : '';
}

export function clearWorkspaceSessionParam(urlValue: string): string {
  const url = new URL(urlValue);
  url.searchParams.delete('session');
  return `${url.pathname}${url.search}${url.hash}`;
}
