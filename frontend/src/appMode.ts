export type ApplicationMode = 'workspace' | 'widget' | 'embed-demo';

export function resolveApplicationMode(
  pathname: string,
  search: string,
): ApplicationMode {
  if (pathname === '/embed-demo') return 'embed-demo';
  const params = new URLSearchParams(search);
  return params.get('mode') === 'widget' ? 'widget' : 'workspace';
}

export function buildWidgetUrl(
  agentUrl: string,
  parentOrigin?: string,
  instanceId?: string,
): string {
  const url = new URL('/?mode=widget', agentUrl);
  if (parentOrigin) url.searchParams.set('parentOrigin', parentOrigin);
  if (instanceId) url.searchParams.set('instanceId', instanceId);
  return url.toString();
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
