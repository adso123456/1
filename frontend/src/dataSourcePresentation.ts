const INTERNAL_SOURCE_ID = /\b(?:postgresql-main|mysql-lzh-monitor|ds_[a-z0-9_-]+)\b/i;
const LEGACY_SWITCH_WORDING = /(?:请)?切换数据源(?:后重试)?|切换当前(?:对话|会话)|重新绑定当前(?:对话|会话)/;

export function formatDatabaseType(type: string): string {
  if (type.toLowerCase() === 'postgresql') return 'PostgreSQL';
  if (type.toLowerCase() === 'mysql') return 'MySQL';
  return '数据库';
}

export function formatDataSourceStatus(
  status: string,
  enabledForChat: boolean,
): string {
  if (status === 'ready') {
    return enabledForChat ? '可问数' : '未开启问数';
  }
  const labels: Record<string, string> = {
    disabled: '已停用',
    draft: '未完成配置',
    connected: '已连接',
    metadata_ready: '待生成资产',
    training_required: '待刷新资产',
    error: '异常',
  };
  return labels[status] || '状态未知';
}

export function sanitizeUserVisibleDataSourceText(text: string): string {
  if (INTERNAL_SOURCE_ID.test(text) || LEGACY_SWITCH_WORDING.test(text)) {
    return '该请求需要在其他数据源中新建对话后重试。';
  }
  return text;
}

export function safeDataSourceDisplayName(name: string): string {
  return INTERNAL_SOURCE_ID.test(name) ? '当前数据源' : name;
}
