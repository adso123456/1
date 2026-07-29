import { useEffect, useMemo, useState } from 'react';
import './ReportPage.css';

type ReportType = 'daily' | 'monthly';

interface IndicatorOption {
  code: number;
  name: string;
  frequencies: number[];
}

interface ReportOptions {
  source_id: string;
  indicators: IndicatorOption[];
  recent_days: number[];
}

interface ReportSummary {
  title: string;
  source_id: string;
  monitoring: {
    valid_station_count: number;
    valid_transmission_rate: number | null;
  };
}

function localDateValue(offsetDays = 0): string {
  const value = new Date();
  value.setDate(value.getDate() + offsetDays);
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function errorMessage(payload: unknown): string {
  if (
    payload
    && typeof payload === 'object'
    && 'detail' in payload
    && typeof payload.detail === 'string'
  ) {
    return payload.detail;
  }
  return '报告生成失败，请稍后重试。';
}

export function ReportPage() {
  const [reportType, setReportType] = useState<ReportType>('daily');
  const [reportDate, setReportDate] = useState(localDateValue(-1));
  const [reportMonth, setReportMonth] = useState(localDateValue().slice(0, 7));
  const [options, setOptions] = useState<ReportOptions | null>(null);
  const [selectedCodes, setSelectedCodes] = useState<number[]>([]);
  const [frequencyHours, setFrequencyHours] = useState<Record<number, string>>({});
  const [recentDays, setRecentDays] = useState(3);
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/reports/water-quality/options', { signal: controller.signal })
      .then(async response => {
        const payload: unknown = await response.json().catch(() => null);
        if (!response.ok) throw new Error(errorMessage(payload));
        return payload as ReportOptions;
      })
      .then(payload => {
        setOptions(payload);
        setSelectedCodes(payload.indicators.map(item => item.code));
        if (!payload.recent_days.includes(3) && payload.recent_days.length) {
          setRecentDays(payload.recent_days[0]);
        }
      })
      .catch(requestError => {
        if (requestError instanceof DOMException && requestError.name === 'AbortError') return;
        setError(requestError instanceof Error ? requestError.message : '筛选项加载失败。');
      });
    return () => controller.abort();
  }, []);

  const parameter = reportType === 'daily' ? reportDate : reportMonth;
  const endpoint = `/api/reports/water-quality/${reportType}`;
  const query = useMemo(() => {
    const value = new URLSearchParams({
      [reportType === 'daily' ? 'date' : 'month']: parameter,
      indicators: selectedCodes.join(','),
    });
    const overrides = selectedCodes
      .filter(code => frequencyHours[code])
      .map(code => `${code}:${frequencyHours[code]}`);
    if (overrides.length) value.set('frequency_hours', overrides.join(','));
    if (reportType === 'daily') value.set('recent_days', String(recentDays));
    return value.toString();
  }, [frequencyHours, parameter, recentDays, reportType, selectedCodes]);
  const downloadUrl = `${endpoint}/pdf?${query}`;

  const clearResult = () => {
    setSummary(null);
    setPreviewUrl(null);
    setError(null);
  };

  const generate = async () => {
    if (!selectedCodes.length) {
      setError('请至少选择一个应测指标。');
      return;
    }
    setLoading(true);
    clearResult();
    try {
      const response = await fetch(`${endpoint}?${query}`);
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(payload));
      setSummary(payload as ReportSummary);
      setPreviewUrl(`${endpoint}/preview?${query}`);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : '报告生成失败，请稍后重试。',
      );
    } finally {
      setLoading(false);
    }
  };

  const switchType = (type: ReportType) => {
    setReportType(type);
    clearResult();
  };

  const toggleIndicator = (code: number) => {
    setSelectedCodes(current => (
      current.includes(code)
        ? current.filter(item => item !== code)
        : [...current, code].sort((left, right) => left - right)
    ));
    clearResult();
  };

  return (
    <section className="report-page">
      <header className="report-page__header">
        <div>
          <h1>日报月报</h1>
          <p>基于真实监测数据和固定规则生成，数据不足时不会补造内容。</p>
        </div>
        <div className="report-source">
          <span>固定数据源</span>
          <strong>{options?.source_id ?? 'mysql-lzh-monitor'}</strong>
        </div>
      </header>

      <div className="report-controls">
        <div className="report-type-tabs" role="tablist" aria-label="报告类型">
          <button
            type="button"
            className={reportType === 'daily' ? 'active' : ''}
            onClick={() => switchType('daily')}
          >
            水质日报
          </button>
          <button
            type="button"
            className={reportType === 'monthly' ? 'active' : ''}
            onClick={() => switchType('monthly')}
          >
            水质月报
          </button>
        </div>

        <label>
          <span>{reportType === 'daily' ? '报告日期' : '报告月份'}</span>
          {reportType === 'daily' ? (
            <input
              aria-label="报告日期"
              type="date"
              value={reportDate}
              onChange={event => { setReportDate(event.target.value); clearResult(); }}
            />
          ) : (
            <input
              aria-label="报告月份"
              type="month"
              value={reportMonth}
              onChange={event => { setReportMonth(event.target.value); clearResult(); }}
            />
          )}
        </label>

        {reportType === 'daily' && (
          <label>
            <span>监测回看范围</span>
            <select
              aria-label="监测回看范围"
              value={recentDays}
              onChange={event => { setRecentDays(Number(event.target.value)); clearResult(); }}
            >
              {(options?.recent_days ?? [1, 2, 3, 5, 7]).map(days => (
                <option key={days} value={days}>近{days}日</option>
              ))}
            </select>
          </label>
        )}

        <div className="report-actions">
          <button
            type="button"
            className="primary"
            disabled={loading || !parameter || !options || !selectedCodes.length}
            onClick={generate}
          >
            {loading ? '生成中…' : summary ? '重新生成' : '生成报告'}
          </button>
          <a
            className={summary ? '' : 'disabled'}
            href={summary ? downloadUrl : undefined}
            aria-disabled={!summary}
          >
            下载 PDF
          </a>
        </div>
      </div>

      <div className="report-indicators">
        <div className="report-indicators__title">
          <strong>应测指标及频次</strong>
          <span>频次默认沿用各站点真实配置，也可按指标统一选择。</span>
        </div>
        <div className="report-indicators__grid">
          {options?.indicators.map(indicator => {
            const checked = selectedCodes.includes(indicator.code);
            return (
              <div className={checked ? 'indicator-option selected' : 'indicator-option'} key={indicator.code}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleIndicator(indicator.code)}
                  />
                  <span>{indicator.name}</span>
                </label>
                <select
                  aria-label={`${indicator.name}监测频次`}
                  value={frequencyHours[indicator.code] ?? ''}
                  disabled={!checked}
                  onChange={event => {
                    setFrequencyHours(current => ({ ...current, [indicator.code]: event.target.value }));
                    clearResult();
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
          {!options && <span className="report-indicators__loading">正在读取真实站点配置…</span>}
        </div>
      </div>

      {error && <div className="report-message report-message--error">{error}</div>}
      {summary && summary.monitoring.valid_station_count === 0 && (
        <div className="report-message">
          所选周期暂无有效监测数据，报告仍按合法空数据结构生成。
        </div>
      )}
      {summary && (
        <div className="report-summary">
          <span>{summary.title}</span>
          <span>有效站点：{summary.monitoring.valid_station_count}</span>
          <span>
            有效传输率：
            {summary.monitoring.valid_transmission_rate === null
              ? '暂无数据'
              : `${summary.monitoring.valid_transmission_rate.toFixed(2)}%`}
          </span>
        </div>
      )}

      <div className="report-preview">
        {loading && <div className="report-preview__empty">正在计算报告，请稍候…</div>}
        {!loading && !previewUrl && (
          <div className="report-preview__empty">
            选择日期、指标和频次后生成报告，即可在此预览。
          </div>
        )}
        {!loading && previewUrl && (
          <iframe title="水质报告预览" src={previewUrl} />
        )}
      </div>
    </section>
  );
}
