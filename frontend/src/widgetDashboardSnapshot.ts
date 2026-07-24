import type { ChartData, DashboardChartItem } from './types';

export interface WidgetDashboardPayload {
  chart: ChartData;
  messageId: string;
  sql: string | null;
}

/** 构造与完整工作台一致的当前图表快照。 */
export function createWidgetDashboardChartItem(
  payload: WidgetDashboardPayload,
  sessionId: string,
  now = Date.now(),
): DashboardChartItem {
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
