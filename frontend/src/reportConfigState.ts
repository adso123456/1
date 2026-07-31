import type {
  ReportConfigData,
  ReportOptions,
  ReportResultData,
} from './components/ReportComponents';

export function configFromReportResult(
  result: ReportResultData,
  options: ReportOptions,
): ReportConfigData {
  return {
    report_type: result.report_type,
    report_type_selectable: false,
    default_date: result.report_type === 'daily' ? result.period : null,
    default_month: result.report_type === 'monthly' ? result.period : null,
    recent_days: result.recent_days ?? 3,
    available_recent_days: options.recent_days,
    available_indicators: options.indicators,
    selected_indicators: result.indicators,
    frequency_hours: result.frequency_hours,
    source_id: options.source_id,
    missing_fields: [],
    error: null,
  };
}
