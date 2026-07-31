import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import type {
  ReportConfigData,
  ReportRequest,
  ReportResultData,
  ReportType,
} from './ReportComponents';
import './ReportComposerPanel.css';

interface Props {
  config: ReportConfigData;
  compact?: boolean;
  theme?: string;
  onChange: (config: ReportConfigData) => void;
  onCancel: () => void;
  onGenerated: (result: ReportResultData) => void;
  requestHeaders?: () => Record<string, string>;
  reportRequest?: ReportRequest;
}

function localDateValue(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

async function responseError(response: Response): Promise<string> {
  const payload: unknown = await response.json().catch(() => null);
  if (
    payload && typeof payload === 'object' && 'detail' in payload
    && typeof payload.detail === 'string'
  ) return payload.detail;
  return '报告生成失败，请稍后重试。';
}

export function ReportComposerPanel({
  config,
  compact = false,
  theme = '#2563eb',
  onChange,
  onCancel,
  onGenerated,
  requestHeaders,
  reportRequest,
}: Props) {
  const [indicatorOpen, setIndicatorOpen] = useState(false);
  const [recentOpen, setRecentOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const selectedOptions = useMemo(
    () => config.selected_indicators
      .map(code => config.available_indicators.find(item => item.code === code))
      .filter(item => item !== undefined),
    [config.available_indicators, config.selected_indicators],
  );

  const update = (patch: Partial<ReportConfigData>) => {
    onChange({
      ...config,
      ...patch,
      error: Object.prototype.hasOwnProperty.call(patch, 'error')
        ? (patch.error ?? null)
        : null,
    });
  };

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!panelRef.current?.contains(event.target as Node)) {
        setIndicatorOpen(false);
        setRecentOpen(false);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIndicatorOpen(false);
        setRecentOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', escape);
    };
  }, []);

  const toggleIndicator = (code: number) => {
    const checked = config.selected_indicators.includes(code);
    const selected = checked
      ? config.selected_indicators.filter(item => item !== code)
      : [...config.selected_indicators, code].sort((a, b) => a - b);
    const frequencies = { ...config.frequency_hours };
    if (checked) delete frequencies[String(code)];
    update({
      selected_indicators: selected,
      frequency_hours: frequencies,
    });
  };

  const setReportType = (reportType: ReportType) => {
    update({
      report_type: reportType,
      default_date: reportType === 'daily'
        ? (config.default_date || localDateValue())
        : null,
      default_month: reportType === 'monthly'
        ? (config.default_month || localDateValue().slice(0, 7))
        : null,
    });
  };

  const generate = async () => {
    if (!config.selected_indicators.length) {
      update({ error: '请至少选择一个应测指标。' });
      return;
    }
    const period = config.report_type === 'daily'
      ? config.default_date
      : config.default_month;
    if (!period) {
      update({
        error: config.report_type === 'daily'
          ? '请选择报告日期。'
          : '请选择报告月份。',
      });
      return;
    }
    setLoading(true);
    update({ error: null });
    try {
      const payload = {
        report_type: config.report_type,
        ...(config.report_type === 'daily'
          ? {
              date: config.default_date,
              recent_days: config.recent_days,
            }
          : { month: config.default_month }),
        indicators: config.selected_indicators,
        frequency_hours: config.frequency_hours,
      };
      const response = reportRequest
        ? await reportRequest('report-generate', payload)
        : await fetch('/api/reports/water-quality/generate', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              ...requestHeaders?.(),
            },
            body: JSON.stringify(payload),
          });
      if (!response.ok) throw new Error(await responseError(response));
      onGenerated(await response.json() as ReportResultData);
    } catch (reason) {
      update({
        error: reason instanceof Error ? reason.message : '报告生成失败。',
      });
    } finally {
      setLoading(false);
    }
  };

  const visibleSummary = selectedOptions.slice(
    0,
    selectedOptions.length > 2 ? 1 : 2,
  );

  return (
    <div
      ref={panelRef}
      className={[
        'report-composer',
        compact ? 'report-composer--compact' : '',
      ].filter(Boolean).join(' ')}
      style={{ '--report-theme': theme } as CSSProperties}
      aria-label={`${config.report_type === 'daily' ? '水质日报' : '水质月报'}配置`}
    >
      <div className="report-composer__title">
        水质{config.report_type === 'daily' ? '日报' : '月报'}配置
      </div>
      <div className="report-composer__controls">
        {config.report_type_selectable && (
          <label className="report-composer__field">
            <span>报告类型</span>
            <select
              aria-label="报告类型"
              disabled={loading}
              value={config.report_type}
              onChange={event => setReportType(event.target.value as ReportType)}
            >
              <option value="daily">水质日报</option>
              <option value="monthly">水质月报</option>
            </select>
          </label>
        )}

        <label className="report-composer__field">
          <span>{config.report_type === 'daily' ? '报告日期' : '报告月份'}</span>
          <input
            aria-label={config.report_type === 'daily' ? '报告日期' : '报告月份'}
            type={config.report_type === 'daily' ? 'date' : 'month'}
            disabled={loading}
            value={
              config.report_type === 'daily'
                ? (config.default_date || '')
                : (config.default_month || '')
            }
            onChange={event => update(
              config.report_type === 'daily'
                ? { default_date: event.target.value }
                : { default_month: event.target.value },
            )}
          />
        </label>

        <div className="report-composer__field report-composer__indicator-field">
          <span>应测指标及频次</span>
          <div className="report-summary">
            <button
              type="button"
              className="report-summary__main"
              aria-expanded={indicatorOpen}
              disabled={loading}
              onClick={() => {
                setIndicatorOpen(current => !current);
                setRecentOpen(false);
              }}
            >
              {visibleSummary.length === 0 && <span>请选择指标</span>}
              {visibleSummary.map(item => (
                <span className="report-summary__chip" key={item.code}>
                  {item.name}
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={`移除${item.name}`}
                    onClick={event => {
                      event.stopPropagation();
                      toggleIndicator(item.code);
                    }}
                    onKeyDown={event => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        event.stopPropagation();
                        toggleIndicator(item.code);
                      }
                    }}
                  >
                    ×
                  </span>
                </span>
              ))}
              {selectedOptions.length > 2 && (
                <span className="report-summary__count">
                  +{selectedOptions.length - 1}
                </span>
              )}
              <span aria-hidden="true">▾</span>
            </button>
          </div>

          {indicatorOpen && (
            <div className="report-indicator-popover" role="dialog" aria-label="选择应测指标及频次">
              <div className="report-indicator-popover__header">
                <strong>应测指标及频次</strong>
                <button type="button" onClick={() => setIndicatorOpen(false)}>完成</button>
              </div>
              <div className="report-indicator-popover__list">
                {config.available_indicators.map(indicator => {
                  const checked = config.selected_indicators.includes(indicator.code);
                  return (
                    <div
                      className={`report-indicator-row${checked ? ' is-selected' : ''}`}
                      key={indicator.code}
                    >
                      <label>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={loading}
                          onChange={() => toggleIndicator(indicator.code)}
                        />
                        <span>{indicator.name}</span>
                      </label>
                      <select
                        aria-label={`${indicator.name}监测频次`}
                        disabled={loading || !checked}
                        value={config.frequency_hours[String(indicator.code)] ?? ''}
                        onChange={event => {
                          const next = { ...config.frequency_hours };
                          if (event.target.value) {
                            next[String(indicator.code)] = Number(event.target.value);
                          } else {
                            delete next[String(indicator.code)];
                          }
                          update({ frequency_hours: next });
                        }}
                      >
                        <option value="">按站点配置</option>
                        {indicator.frequencies.map(hours => (
                          <option key={hours} value={hours}>{hours}小时1次</option>
                        ))}
                      </select>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {config.report_type === 'daily' && (
          <div className="report-composer__field report-composer__recent-field">
            <span>回看范围</span>
            <button
              type="button"
              className="report-recent-button"
              aria-expanded={recentOpen}
              disabled={loading}
              onClick={() => {
                setRecentOpen(current => !current);
                setIndicatorOpen(false);
              }}
            >
              近{config.recent_days}日 <span aria-hidden="true">▾</span>
            </button>
            {recentOpen && (
              <div className="report-recent-menu" role="listbox" aria-label="回看范围">
                {config.available_recent_days.map(days => (
                  <button
                    type="button"
                    role="option"
                    aria-selected={days === config.recent_days}
                    className={days === config.recent_days ? 'is-selected' : ''}
                    key={days}
                    onClick={() => {
                      update({ recent_days: days });
                      setRecentOpen(false);
                    }}
                  >
                    近{days}日
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="report-composer__actions">
          <button
            type="button"
            className="report-composer__cancel"
            disabled={loading}
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            className="report-composer__submit"
            disabled={loading || !config.selected_indicators.length}
            onClick={() => void generate()}
          >
            {loading ? '生成中…' : '生成预览'}
          </button>
        </div>
      </div>
      {config.error && (
        <div className="report-composer__error" role="alert">{config.error}</div>
      )}
    </div>
  );
}
