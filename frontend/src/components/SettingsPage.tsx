import { useCallback, useEffect, useState } from 'react';
import './SettingsPage.css';

interface LLMSettings {
  api_key_set: boolean;
  model: string;
  base_url: string;
  persisted: boolean;
}

interface TestResult {
  ok: boolean;
  model: string;
  base_url: string;
  latency_ms: number;
  error: string;
}

interface LearningSettings {
  enabled: boolean;
  capture_enabled: boolean;
  judge_enabled: boolean;
  auto_publish: boolean;
}

interface LogEntry {
  ts: number;
  level: string;
  logger: string;
  message: string;
}

const DEFAULT_BASE_URL = 'https://api.deepseek.com';

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [learningForm, setLearningForm] = useState<LearningSettings>({
    enabled: false,
    capture_enabled: true,
    judge_enabled: true,
    auto_publish: false,
  });
  const [learningSaving, setLearningSaving] = useState(false);
  const [learningNotice, setLearningNotice] = useState<{
    ok: boolean;
    text: string;
  } | null>(null);
  const [logLevel, setLogLevel] = useState('INFO');
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logTotal, setLogTotal] = useState(0);
  const [logLoading, setLogLoading] = useState(false);

  const loadLogs = useCallback(async () => {
    setLogLoading(true);
    try {
      const response = await fetch(
        `/api/admin/system/logs?level=${encodeURIComponent(logLevel)}&limit=200`,
        { method: 'GET' },
      );
      if (response.ok) {
        const payload = (await response.json()) as { logs: LogEntry[]; total: number };
        setLogs(payload.logs);
        setLogTotal(payload.total);
      }
    } catch {
      setLogs([]);
      setLogTotal(0);
    } finally {
      setLogLoading(false);
    }
  }, [logLevel]);

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/admin/llm-settings', { method: 'GET' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as LLMSettings;
      setSettings(data);
      setModel(data.model);
      setBaseUrl(data.base_url || DEFAULT_BASE_URL);
    } catch {
      setNotice({ ok: false, text: '读取 LLM 配置失败，请确认管理接口可用。' });
    }
    try {
      const response = await fetch('/api/admin/runtime-learning/status', {
        method: 'GET',
      });
      if (response.ok) {
        const status = (await response.json()) as {
          counts?: { settings?: LearningSettings };
        };
        const current = status.counts?.settings;
        if (current) {
          setLearningForm({
            enabled: current.enabled,
            capture_enabled: current.capture_enabled,
            judge_enabled: current.judge_enabled,
            auto_publish: current.auto_publish,
          });
        }
      }
    } catch {
      // 学习接口不可用时保持默认表单值。
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const response = await fetch('/api/admin/llm-settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: apiKey.trim() || undefined,
          model: model.trim(),
          base_url: baseUrl.trim() || undefined,
        }),
      });
      const payload = (await response.json()) as LLMSettings & { detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      setSettings(payload);
      setApiKey('');
      setModel(payload.model);
      setBaseUrl(payload.base_url || DEFAULT_BASE_URL);
      setTestResult(null);
      setNotice({ ok: true, text: '已保存，下一次问答即使用新配置。' });
    } catch (error) {
      setNotice({
        ok: false,
        text: `保存失败：${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const response = await fetch('/api/admin/llm-settings/test', {
        method: 'POST',
      });
      const payload = (await response.json()) as TestResult & { detail?: string };
      if (!response.ok) {
        setTestResult({
          ok: false,
          model: settings?.model || '',
          base_url: settings?.base_url || '',
          latency_ms: 0,
          error: payload.detail || `HTTP ${response.status}`,
        });
      } else {
        setTestResult(payload);
      }
    } catch (error) {
      setTestResult({
        ok: false,
        model: settings?.model || '',
        base_url: settings?.base_url || '',
        latency_ms: 0,
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setTesting(false);
    }
  };

  const saveLearning = async () => {
    setLearningSaving(true);
    setLearningNotice(null);
    try {
      const response = await fetch('/api/admin/runtime-learning/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(learningForm),
      });
      const payload = (await response.json()) as { detail?: string };
      if (!response.ok) {
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }
      setLearningNotice({ ok: true, text: '已保存，学习链路立即按新开关运行。' });
    } catch (error) {
      setLearningNotice({
        ok: false,
        text: `保存失败：${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setLearningSaving(false);
    }
  };

  const setLearningField = (key: keyof LearningSettings, value: boolean) => {
    setLearningForm(current => ({ ...current, [key]: value }));
  };

  return (
    <div className="settings-page">
      <header className="settings-header">
        <div className="settings-heading">
          <h1>设置</h1>
          <p>运行时模型、学习链路、系统状态与维护配置</p>
        </div>
      </header>

      <div className="settings-grid">
        <section className="settings-card">
          <h2>LLM 配置</h2>
          <p className="settings-sub">
            本项目仅支持 OpenAI 兼容格式的接口（如
            {' '}
            <code>https://api.deepseek.com</code>
            ）。保存后后端立即更换 Key / 模型 / 地址，下一次问答生效。
          </p>
          <div className="settings-form-grid">
            <label className="settings-span2">
              API Key
              <input
                type="password"
                value={apiKey}
                onChange={event => setApiKey(event.target.value)}
                placeholder={
                  settings?.api_key_set
                    ? '已配置（留空保持不变）'
                    : '请输入 API Key'
                }
                autoComplete="off"
              />
            </label>
            <label>
              模型名称
              <input
                type="text"
                value={model}
                onChange={event => setModel(event.target.value)}
                placeholder="如 deepseek-v4-flash"
              />
            </label>
            <label>
              Base URL
              <input
                type="text"
                value={baseUrl}
                onChange={event => setBaseUrl(event.target.value)}
                placeholder={DEFAULT_BASE_URL}
              />
            </label>
          </div>
          <div className="settings-actions">
            <button
              type="button"
              className="settings-primary"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? '保存中…' : '保存配置'}
            </button>
            <button
              type="button"
              disabled={testing || !settings?.api_key_set}
              onClick={() => void testConnection()}
              title={
                settings?.api_key_set
                  ? '用当前配置发送最小请求验证连通性'
                  : '请先配置 API Key'
              }
            >
              {testing ? '测试中…' : '测试连接'}
            </button>
            {notice && (
              <span
                className={
                  `settings-notice ${notice.ok
                    ? 'settings-notice--ok'
                    : 'settings-notice--error'}`
                }
              >
                {notice.text}
              </span>
            )}
          </div>
          {testResult && (
            <div
              className={
                `settings-test-result ${testResult.ok
                  ? 'settings-test-result--ok'
                  : 'settings-test-result--error'}`
              }
            >
              {testResult.ok
                ? `连接成功：${testResult.model} @ ${testResult.base_url}，耗时 ${testResult.latency_ms}ms`
                : `连接失败：${testResult.error || '未知错误'}`}
            </div>
          )}
        </section>

        <section className="settings-card">
          <h2>运行时学习</h2>
          <p className="settings-sub">
            捕获成功问答 → LLM 判定 → 微批次发布。改动即时生效，无需重启。
          </p>
          <div className="settings-switches">
            <label className="settings-switch-row">
              <span>
                启用运行时学习
                <small>总开关，关闭后不再捕获新候选</small>
              </span>
              <span className="settings-switch">
                <input
                  type="checkbox"
                  checked={learningForm.enabled}
                  onChange={event => setLearningField('enabled', event.target.checked)}
                />
                <span className="track" />
              </span>
            </label>
            <label className="settings-switch-row">
              <span>
                捕获候选
                <small>记录成功问答（受 SQLGuard 等硬门禁约束）</small>
              </span>
              <span className="settings-switch">
                <input
                  type="checkbox"
                  checked={learningForm.capture_enabled}
                  onChange={event =>
                    setLearningField('capture_enabled', event.target.checked)
                  }
                />
                <span className="track" />
              </span>
            </label>
            <label className="settings-switch-row">
              <span>
                自动判定（Judge）
                <small>用 LLM 检查问题 / SQL / 结果 / 回答一致性</small>
              </span>
              <span className="settings-switch">
                <input
                  type="checkbox"
                  checked={learningForm.judge_enabled}
                  onChange={event =>
                    setLearningField('judge_enabled', event.target.checked)
                  }
                />
                <span className="track" />
              </span>
            </label>
            <label className="settings-switch-row">
              <span>
                自动发布
                <small>PASS 候选达到批次条件后自动发布到正式内存</small>
              </span>
              <span className="settings-switch">
                <input
                  type="checkbox"
                  checked={learningForm.auto_publish}
                  onChange={event =>
                    setLearningField('auto_publish', event.target.checked)
                  }
                />
                <span className="track" />
              </span>
            </label>
          </div>
          <div className="settings-actions">
            <button
              type="button"
              className="settings-primary"
              disabled={learningSaving}
              onClick={() => void saveLearning()}
            >
              {learningSaving ? '保存中…' : '保存学习开关'}
            </button>
            {learningNotice && (
              <span
                className={
                  `settings-notice ${learningNotice.ok
                    ? 'settings-notice--ok'
                    : 'settings-notice--error'}`
                }
              >
                {learningNotice.text}
              </span>
            )}
          </div>
        </section>

        <section className="settings-card" style={{ gridColumn: '1 / -1' }}>
          <h2>系统日志</h2>
          <p className="settings-sub">
            最近日志实时读取自服务进程，用于排查问题。
          </p>
          <div className="settings-log-toolbar">
            <select
              value={logLevel}
              onChange={event => setLogLevel(event.target.value)}
              aria-label="日志级别"
            >
              <option value="INFO">全部</option>
              <option value="WARNING">警告及以上</option>
              <option value="ERROR">错误</option>
            </select>
            <button
              type="button"
              disabled={logLoading}
              onClick={() => void loadLogs()}
            >
              {logLoading ? '刷新中…' : '刷新'}
            </button>
            <span>共 {logTotal} 条匹配日志</span>
          </div>
          <div className="settings-terminal" role="log">
            {logs.length === 0 && (
              <div className="settings-terminal-empty">
                {logLoading ? '加载日志中…' : '暂无日志'}
              </div>
            )}
            {logs.map((entry, index) => (
              <div className="settings-terminal-line" key={`${entry.ts}-${index}`}>
                <span className="settings-terminal-time">
                  {formatTime(entry.ts)}
                </span>
                <span
                  className={
                    `settings-terminal-level settings-terminal-level--${entry.level}`
                  }
                >
                  {entry.level}
                </span>
                <span className="settings-terminal-message">{entry.message}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
