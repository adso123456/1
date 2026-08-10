import type { ChartData, DashboardItem } from './types';

export interface WidgetDashboardPayload {
  chart: ChartData;
  viewMode: 'chart' | 'table';
  messageId: string;
  sql: string | null;
}

/** 构造与完整工作台一致的当前图表快照。 */
export function createWidgetDashboardChartItem(
  payload: WidgetDashboardPayload,
  sessionId: string,
  now = Date.now(),
): DashboardItem {
  if (payload.viewMode === 'table') {
    return {
      type: 'table',
      id: `${sessionId}::${payload.messageId}::${payload.chart.id}::table`,
      sourceSessionId: sessionId,
      sourceMessageId: payload.messageId,
      addedAt: now,
      table: {
        data: JSON.parse(JSON.stringify(payload.chart.rows)),
        columns: [...payload.chart.columns],
        row_count: payload.chart.rows.length,
        column_count: payload.chart.columns.length,
        title: payload.chart.title,
      },
    };
  }
  return {
    type: 'chart',
    id: `${sessionId}::${payload.messageId}::${payload.chart.id}`,
    sourceSessionId: sessionId,
    sourceMessageId: payload.messageId,
    addedAt: now,
    chart: JSON.parse(JSON.stringify(payload.chart)) as ChartData,
    sourceSql: payload.sql,
    lastRefreshedAt: now,
  };
}
