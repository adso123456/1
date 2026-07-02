import { useState, useMemo, useEffect, useRef } from 'react';
import type { DashboardMeta } from '../types';

interface Props {
  /** 全部仪表板（用于下拉选择与重名校验） */
  dashboards: DashboardMeta[];
  /** 当前仪表板 ID（已有模式下默认选中） */
  currentDashboardId: string;
  /** 确认：返回目标仪表板（已有 id 或新建名称），由上层构造卡片并写入 */
  onConfirm: (target: { mode: 'existing'; dashboardId: string } | { mode: 'new'; name: string }) => void;
  onClose: () => void;
}

type Mode = 'existing' | 'new';

const BORDER = '#e5e7eb';
const TEXT_PRIMARY = '#1f2937';
const TEXT_SECONDARY = '#6b7280';
const TEXT_MUTED = '#9ca3af';
const ACTIVE_TEXT = '#2563eb';
const ERROR_COLOR = '#dc2626';

export function AddToDashboardDialog({ dashboards, currentDashboardId, onConfirm, onClose }: Props) {
  const [mode, setMode] = useState<Mode>('existing');
  // 已有模式默认选中当前仪表板；若当前 ID 无效则取第一个
  const initialExisting = dashboards.find(d => d.id === currentDashboardId)?.id
    ?? (dashboards.length > 0 ? dashboards[0].id : '');
  const [selectedId, setSelectedId] = useState<string>(initialExisting);
  const [newName, setNewName] = useState<string>('');
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 切到"新建"模式时聚焦输入框
  useEffect(() => {
    if (mode === 'new') {
      // 等一帧确保 input 已渲染
      requestAnimationFrame(() => inputRef.current?.focus());
    } else {
      setError(null);
    }
  }, [mode]);

  // ESC 关闭
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !submitting) onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose, submitting]);

  // 新建名称校验：trim 后长度 1~64 且不与已有仪表板重名
  const newModeError = useMemo<string | null>(() => {
    if (mode !== 'new') return null;
    const trimmed = newName.trim();
    if (trimmed.length === 0) return '请输入仪表板名称';
    if (trimmed.length > 64) return '名称不能超过 64 个字符';
    if (dashboards.some(d => d.name === trimmed)) return '该名称已存在，请更换';
    return null;
  }, [mode, newName, dashboards]);

  // 是否可提交
  const canSubmit = !submitting && (
    mode === 'existing' ? !!selectedId : newModeError === null
  );

  const handleConfirm = () => {
    if (submitting) return;
    if (mode === 'existing') {
      if (!selectedId) {
        setError('请选择一个仪表板');
        return;
      }
      setSubmitting(true);
      onConfirm({ mode: 'existing', dashboardId: selectedId });
    } else {
      if (newModeError) {
        setError(newModeError);
        return;
      }
      setSubmitting(true);
      onConfirm({ mode: 'new', name: newName.trim() });
    }
  };

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (submitting) return;
    if (e.target === e.currentTarget) onClose();
  };

  return (
    <div
      onClick={handleBackdropClick}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        backgroundColor: 'rgba(0,0,0,0.35)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          width: '90vw',
          maxWidth: 440,
          backgroundColor: '#fff',
          borderRadius: 12,
          boxShadow: '0 8px 40px rgba(0,0,0,0.18)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* 标题栏 */}
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 20px',
          borderBottom: `1px solid ${BORDER}`,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 16, fontWeight: 600, color: TEXT_PRIMARY }}>
            添加到仪表板
          </span>
          <button
            onClick={onClose}
            disabled={submitting}
            title="关闭"
            aria-label="关闭"
            style={{
              width: 28,
              height: 28,
              border: 'none',
              borderRadius: 6,
              backgroundColor: 'transparent',
              color: TEXT_MUTED,
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontSize: 18,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            ×
          </button>
        </div>

        {/* 主体 */}
        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 单选：已有 / 新建 */}
          <div style={{ display: 'flex', gap: 12 }}>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 13,
                color: TEXT_PRIMARY,
                cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              <input
                type="radio"
                name="add-target"
                checked={mode === 'existing'}
                disabled={submitting || dashboards.length === 0}
                onChange={() => setMode('existing')}
                style={{ cursor: 'pointer' }}
              />
              已有仪表板
            </label>
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 13,
                color: TEXT_PRIMARY,
                cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              <input
                type="radio"
                name="add-target"
                checked={mode === 'new'}
                disabled={submitting}
                onChange={() => setMode('new')}
                style={{ cursor: 'pointer' }}
              />
              新建仪表板
            </label>
          </div>

          {/* 已有模式：下拉选择 */}
          {mode === 'existing' && (
            <div>
              {dashboards.length === 0 ? (
                <div style={{ fontSize: 12, color: TEXT_MUTED, padding: '8px 0' }}>
                  暂无仪表板，请选择「新建仪表板」。
                </div>
              ) : (
                <select
                  value={selectedId}
                  onChange={e => setSelectedId(e.target.value)}
                  disabled={submitting}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    border: `1px solid ${BORDER}`,
                    borderRadius: 6,
                    fontSize: 13,
                    color: TEXT_PRIMARY,
                    backgroundColor: '#fff',
                    outline: 'none',
                    cursor: 'pointer',
                    boxSizing: 'border-box',
                  }}
                >
                  {dashboards.map(d => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              )}
            </div>
          )}

          {/* 新建模式：名称输入 */}
          {mode === 'new' && (
            <div>
              <input
                ref={inputRef}
                type="text"
                value={newName}
                onChange={e => { setNewName(e.target.value); setError(null); }}
                disabled={submitting}
                placeholder="输入新仪表板名称"
                maxLength={64}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  border: `1px solid ${newModeError ? ERROR_COLOR : BORDER}`,
                  borderRadius: 6,
                  fontSize: 13,
                  color: TEXT_PRIMARY,
                  backgroundColor: '#fff',
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                marginTop: 4,
                fontSize: 11,
                color: newModeError ? ERROR_COLOR : TEXT_MUTED,
              }}>
                <span>{newModeError ?? '名称长度 1～64，不可与已有仪表板重名'}</span>
                <span>{newName.trim().length}/64</span>
              </div>
            </div>
          )}

          {/* 错误提示（已有模式等） */}
          {error && mode === 'existing' && (
            <div style={{ fontSize: 12, color: ERROR_COLOR }}>{error}</div>
          )}
        </div>

        {/* 底部按钮 */}
        <div style={{
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8,
          padding: '12px 20px',
          borderTop: `1px solid ${BORDER}`,
          flexShrink: 0,
        }}>
          <button
            onClick={onClose}
            disabled={submitting}
            style={{
              padding: '8px 20px',
              border: `1px solid ${BORDER}`,
              borderRadius: 6,
              backgroundColor: '#fff',
              color: TEXT_SECONDARY,
              cursor: submitting ? 'not-allowed' : 'pointer',
              fontSize: 13,
              fontWeight: 500,
            }}
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={!canSubmit}
            style={{
              padding: '8px 20px',
              border: 'none',
              borderRadius: 6,
              backgroundColor: canSubmit ? ACTIVE_TEXT : '#d1d5db',
              color: '#fff',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
              fontSize: 13,
              fontWeight: 500,
              transition: 'background-color .15s',
            }}
          >
            {submitting ? '添加中…' : '确认'}
          </button>
        </div>
      </div>
    </div>
  );
}
