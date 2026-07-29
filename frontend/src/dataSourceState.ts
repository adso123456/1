import type { DataSourceSummary } from './types';

const STATUS_REASON: Record<string, string> = {
  draft: '当前数据源未完成配置',
  connected: '当前数据源尚未准备问数资产',
  metadata_ready: '当前数据源尚未准备问数资产',
  training_required: '当前数据源需要刷新问数资产',
  disabled: '当前数据源已停用',
  error: '当前数据源发生错误',
};

export function dataSourceUnavailableReason(
  source: DataSourceSummary | undefined,
  sourceBound: boolean,
): string {
  if (!sourceBound) return '当前会话未绑定数据源';
  if (!source) return '当前数据源不可用';
  if (source.status !== 'ready') {
    return STATUS_REASON[source.status] || '当前数据源不可用于问数';
  }
  if (!source.enabled_for_chat) return '当前数据源已停用';
  return '';
}

export function canSendToDataSource(
  source: DataSourceSummary | undefined,
  sourceBound: boolean,
): boolean {
  return (
    sourceBound
    && source?.status === 'ready'
    && source.enabled_for_chat === true
  );
}
