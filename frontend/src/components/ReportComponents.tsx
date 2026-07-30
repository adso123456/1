import { useEffect, useMemo, useState } from 'react';
import './ReportComponents.css';

export type ReportType = 'daily' | 'monthly';

export interface IndicatorOption {
  code: number;
  name: string;
  frequencies: number[];
}

export interface ReportOptions {
  source_id: string;
  indicators: IndicatorOption[];
  recent_days: number[];
}

export interface ReportConfigData {
  report_type: ReportType;
  report_type_selectable: boolean;
  default_date?: string | null;
  default_month?: string | null;
  recent_days: number;
  available_recent_days: number[];
  available_indicators: IndicatorOption[];
  selected_indicators: number[];
  frequency_hours: Record<string, number>;
  source_id: string;
  missing_fields: string[];
  error?: string | null;
}

export interface ReportResultData {
  report_id: string;
  report_type: ReportType;
  title: string;
  period: string;
  indicators: number[];
  indicator_names?: string[];
  recent_days?: number | null;
  frequency_hours: Record<string, number>;
  source_id: string;
  preview_url: string;
  download_url: string;
  status: string;
}

function localDateValue(): string {
  const value = new Date();
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
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

interface ParameterProps {
  reportType: ReportType;
  initialDate?: string | null;
  initialMonth?: string | null;
  initialRecentDays?: number;
  compact?: boolean;
  submitLabel?: string;
  onGenerated: (result: ReportResultData) => void;
}

export function ReportParameterForm({
  reportType,
  initialDate,
  initialMonth,
  initialRecentDays = 3,
  compact = false,
  submitLabel = '生成预览',
  onGenerated,
}: ParameterProps) {
  const [options, setOptions] = useState<ReportOptions | null>(null);
  const [period, setPeriod] = useState(
    reportType === 'daily'
      ? (initialDate || localDateValue())
      : (initialMonth || localDateValue().slice(0, 7)),
  );
  const [selected, setSelected] = useState<number[]>([]);
  const [frequencies, setFrequencies] = useState<Record<number, string>>({});
  const [recentDays, setRecentDays] = useState(initialRecentDays);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetch('/api/reports/water-quality/options')
      .then(async response => {
        if (!response.ok) throw new Error(await responseError(response));
        return response.json() as Promise<ReportOptions>;
      })
      .then(payload => {
        if (!active) return;
        setOptions(payload);
        setSelected(payload.indicators.map(item => item.code));
      })
      .catch(reason => {
        if (active) setError(reason instanceof Error ? reason.message : '筛选项加载失败。');
      });
    return () => { active = false; };
  }, []);

  const requestBody = useMemo(() => {
    const frequencyHours = Object.fromEntries(
      selected
        .filter(code => frequencies[code])
        .map(code => [String(code), Number(frequencies[code])]),
    );
    return {
      report_type: reportType,
      ...(reportType === 'daily' ? { date: period, recent_days: recentDays } : { month: period }),
      indicators: selected,
      frequency_hours: frequencyHours,
    };
  }, [frequencies, period, recentDays, reportType, selected]);

  const generate = async () => {
    if (!selected.length) {
      setError('请至少选择一个应测指标。');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/reports/water-quality/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });
      if (!response.ok) throw new Error(await responseError(response));
      onGenerated(await response.json() as ReportResultData);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '报告生成失败。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={compact ? 'report-parameter report-parameter--compact' : 'report-parameter'}>
      <div className="report-parameter__top">
        <label>
          <span>{reportType === 'daily' ? '报告日期' : '报告月份'}</span>
          <input
            aria-label={reportType === 'daily' ? '报告日期' : '报告月份'}
            type={reportType === 'daily' ? 'date' : 'month'}
            value={period}
            onChange={event => setPeriod(event.target.value)}
          />
        </label>
        {reportType === 'daily' && (
          <label>
            <span>回看范围</span>
            <select
              aria-label="回看范围"
              value={recentDays}
              onChange={event => setRecentDays(Number(event.target.value))}
            >
              {(options?.recent_days ?? [2, 3, 4, 5, 6, 7]).map(days => (
                <option key={days} value={days}>近{days}日</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="report-parameter__heading">
        <strong>应测指标及频次</strong>
        <span>默认使用各站真实频次；月报与日报使用相同指标选择。</span>
      </div>
      <div className="report-parameter__indicators">
        {options?.indicators.map(indicator => {
          const checked = selected.includes(indicator.code);
          return (
            <div className={checked ? 'report-indicator selected' : 'report-indicator'} key={indicator.code}>
              <label>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => setSelected(current => (
                    checked
                      ? current.filter(code => code !== indicator.code)
                      : [...current, indicator.code].sort((a, b) => a - b)
                  ))}
                />
                <span>{indicator.name}</span>
              </label>
              <select
                aria-label={`${indicator.name}监测频次`}
                disabled={!checked}
                value={frequencies[indicator.code] ?? ''}
                onChange={event => setFrequencies(current => ({
                  ...current,
                  [indicator.code]: event.target.value,
                }))}
              >
                <option value="">按站点配置</option>
                {indicator.frequencies.map(hours => (
                  <option key={hours} value={hours}>{hours}小时1次</option>
                ))}
              </select>
            </div>
          );
        })}
        {!options && !error && <span className="report-muted">正在读取真实站点配置…</span>}
      </div>
      {error && <div className="report-inline-error">{error}</div>}
      <div className="report-parameter__actions">
        <button
          type="button"
          className="report-primary"
          disabled={loading || !options || !period || !selected.length}
          onClick={generate}
        >
          {loading ? '生成中…' : submitLabel}
        </button>
      </div>
    </div>
  );
}

export function ReportConfigCard({
  config,
  onGenerated,
}: {
  config: ReportConfigData;
  onGenerated: (result: ReportResultData) => void;
}) {
  return (
    <div className="report-card">
      <div className="report-card__title">
        配置{config.report_type === 'daily' ? '水质日报' : '水质月报'}
      </div>
      {config.error && <div className="report-inline-error">{config.error}</div>}
      <ReportParameterForm
        compact
        reportType={config.report_type}
        initialDate={config.default_date}
        initialMonth={config.default_month}
        initialRecentDays={config.recent_days}
        onGenerated={onGenerated}
      />
    </div>
  );
}

export function ReportResultCard({
  result,
  onPreview,
  onReconfigure,
}: {
  result: ReportResultData;
  onPreview: (result: ReportResultData) => void;
  onReconfigure?: (result: ReportResultData) => void;
}) {
  return (
    <div className="report-card report-result-card">
      <div className="report-card__title">{result.title}已生成</div>
      <div>报告{result.report_type === 'daily' ? '日期' : '月份'}：{result.period}</div>
      <div>监测指标：{result.indicator_names?.join('、') || '已选择指标'}</div>
      {result.report_type === 'daily' && <div>回看范围：近{result.recent_days}日</div>}
      <div className="report-result-card__actions">
        <button type="button" className="report-primary" onClick={() => onPreview(result)}>
          预览报告
        </button>
        {onReconfigure && (
          <button
            type="button"
            className="report-secondary"
            onClick={() => onReconfigure(result)}
          >
            修改配置重新生成
          </button>
        )}
      </div>
    </div>
  );
}

export function ReportPreviewModal({
  result,
  onClose,
}: {
  result: ReportResultData | null;
  onClose: () => void;
}) {
  if (!result) return null;
  return (
    <div className="report-preview-overlay" role="dialog" aria-modal="true" aria-label="报告预览">
      <div className="report-preview-modal">
        <header>
          <div><strong>{result.title}</strong><span>{result.period}</span></div>
          <div>
            <a href={result.download_url}>导出 PDF</a>
            <button type="button" onClick={onClose}>关闭</button>
          </div>
        </header>
        <div className="report-preview-modal__body">
          <iframe title="水质报告预览" src={result.preview_url} />
        </div>
      </div>
    </div>
  );
}
