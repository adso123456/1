export type ApplicationMode = 'workspace' | 'widget' | 'embed-demo';

export function resolveApplicationMode(
  pathname: string,
  search: string,
): ApplicationMode {
  if (pathname === '/embed-demo') return 'embed-demo';
  const params = new URLSearchParams(search);
  return params.get('mode') === 'widget' ? 'widget' : 'workspace';
}

export function buildWidgetUrl(agentUrl: string): string {
  return new URL('/?mode=widget', agentUrl).toString();
}

export function buildWorkspaceUrl(agentUrl: string): string {
  return new URL('/', agentUrl).toString();
}
