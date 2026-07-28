import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  AdminDataSource,
  AssistantApplicationView,
} from '../../adminTypes';
import {
  generateEmbedCode,
  normalizeHttpOrigin,
} from '../../embedCodeGenerator';

interface EmbedCodeDialogProps {
  application: AssistantApplicationView;
  dataSources: AdminDataSource[];
  onClose: () => void;
}

type CodeKey = 'browserHtml' | 'pythonFastApi' | 'environment';

export function EmbedCodeDialog({
  application,
  dataSources,
  onClose,
}: EmbedCodeDialogProps) {
  const snapshotRef = useRef(1);
  const [selectedOrigin, setSelectedOrigin] = useState(
    application.allowed_origins[0] ?? '',
  );
  const [agentOrigin, setAgentOrigin] = useState(window.location.origin);
  const [copyStatus, setCopyStatus] = useState('');
  const knownIds = useMemo(
    () => new Set(dataSources.map(source => source.source_id)),
    [dataSources],
  );
  const effectiveSources = application.allowed_source_ids.filter(
    sourceId => knownIds.has(sourceId),
  );
  const staleSources = application.allowed_source_ids.filter(
    sourceId => !knownIds.has(sourceId),
  );
  const normalizedAgentOrigin = normalizeHttpOrigin(agentOrigin);
  const canGenerate = Boolean(
    selectedOrigin
    && effectiveSources.length
    && normalizedAgentOrigin,
  );
  const code = useMemo(() => {
    if (!canGenerate) return null;
    try {
      return generateEmbedCode({
        appId: application.app_id,
        parentOrigin: selectedOrigin,
        allowedSourceIds: effectiveSources,
        tokenTtlSeconds: application.token_ttl_seconds,
        agentOrigin: normalizedAgentOrigin,
      });
    } catch {
      return null;
    }
  }, [
    application.app_id,
    application.token_ttl_seconds,
    canGenerate,
    effectiveSources,
    normalizedAgentOrigin,
    selectedOrigin,
  ]);

  useEffect(() => {
    snapshotRef.current += 1;
    setCopyStatus('');
  }, [selectedOrigin, agentOrigin]);

  const close = () => {
    snapshotRef.current += 1;
    onClose();
  };

  const copy = async (key: CodeKey) => {
    if (!code) return;
    const snapshot = snapshotRef.current;
    const value = code[key];
    try {
      await navigator.clipboard.writeText(value);
      if (snapshotRef.current === snapshot) {
        setCopyStatus('已复制当前代码快照。');
      }
    } catch {
      if (snapshotRef.current === snapshot) {
        setCopyStatus('复制失败，请手工选择代码。');
      }
    }
  };

  return (
    <div className="admin-modal-backdrop" role="presentation">
      <section
        className="admin-modal admin-code-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-code-title"
      >
        <div className="admin-modal-header">
          <div>
            <p className="admin-eyebrow">受保护嵌入代码</p>
            <h2 id="admin-code-title">{application.name}</h2>
            <code>{application.app_id}</code>
          </div>
          <button className="admin-button" type="button" onClick={close}>
            关闭
          </button>
        </div>
        {!application.enabled && (
          <p className="admin-code-warning">
            应用当前已禁用，启用后嵌入才能使用。
          </p>
        )}
        {staleSources.length > 0 && (
          <p className="admin-code-warning">
            已排除失效数据源授权：{staleSources.join('、')}
          </p>
        )}
        <div className="admin-code-settings">
          <label>
            允许 Origin
            <select
              value={selectedOrigin}
              onChange={event => setSelectedOrigin(event.target.value)}
            >
              {application.allowed_origins.map(origin => (
                <option key={origin} value={origin}>{origin}</option>
              ))}
            </select>
          </label>
          <label>
            Agent Origin
            <input
              value={agentOrigin}
              onChange={event => setAgentOrigin(event.target.value)}
              spellCheck={false}
            />
          </label>
        </div>
        {!application.allowed_origins.length && (
          <p className="admin-inline-error">
            请先编辑应用并配置允许 Origin。
          </p>
        )}
        {!effectiveSources.length && (
          <p className="admin-inline-error">
            请先编辑应用并配置当前有效的数据源授权。
          </p>
        )}
        {agentOrigin && !normalizedAgentOrigin && (
          <p className="admin-inline-error">
            Agent URL 必须是精确的 http/https Origin，不能包含路径、查询或片段。
          </p>
        )}
        {code && (
          <>
            <CodeBlock
              title="浏览器 HTML"
              value={code.browserHtml}
              onCopy={() => void copy('browserHtml')}
            />
            <CodeBlock
              title="Python / FastAPI Token 接口"
              value={code.pythonFastApi}
              onCopy={() => void copy('pythonFastApi')}
            />
            <CodeBlock
              title="服务端环境变量"
              value={code.environment}
              onCopy={() => void copy('environment')}
            />
            <p className="admin-secret-warning">
              应用 Secret 只在创建或轮换时显示一次。如果未保存，只能先轮换
              Secret，再更新宿主服务端配置。
            </p>
          </>
        )}
        {copyStatus && (
          <p className="admin-copy-status" role="status">{copyStatus}</p>
        )}
      </section>
    </div>
  );
}

function CodeBlock({
  title,
  value,
  onCopy,
}: {
  title: string;
  value: string;
  onCopy: () => void;
}) {
  return (
    <section className="admin-code-block">
      <div>
        <h3>{title}</h3>
        <button className="admin-button" type="button" onClick={onCopy}>
          复制
        </button>
      </div>
      <pre><code>{value}</code></pre>
    </section>
  );
}
