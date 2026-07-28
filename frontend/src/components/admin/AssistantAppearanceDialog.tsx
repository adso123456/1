import { useState, type CSSProperties, type FormEvent } from 'react';
import {
  DEFAULT_ASSISTANT_APPEARANCE,
  normalizeAssistantAppearance,
  validateAssistantAppearance,
  type AssistantAppearance,
} from '../../assistantAppearance';
import type { AssistantApplicationView } from '../../adminTypes';

interface AssistantAppearanceDialogProps {
  application: AssistantApplicationView;
  saving: boolean;
  requestError: string;
  onClose: () => void;
  onSave: (appearance: AssistantAppearance) => void;
}

function numericValue(value: string): number {
  return value === '' ? Number.NaN : Number(value);
}

export function AssistantAppearanceDialog({
  application,
  saving,
  requestError,
  onClose,
  onSave,
}: AssistantAppearanceDialogProps) {
  const [appearance, setAppearance] = useState<AssistantAppearance>(
    normalizeAssistantAppearance(application),
  );
  const [validationError, setValidationError] = useState('');

  const update = <Key extends keyof AssistantAppearance>(
    key: Key,
    value: AssistantAppearance[Key],
  ) => {
    setAppearance(current => ({ ...current, [key]: value }));
    setValidationError('');
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const normalized = {
      ...appearance,
      theme: appearance.theme.trim().toLowerCase(),
      header_font_color:
        appearance.header_font_color.trim().toLowerCase(),
      logo_url: appearance.logo_url.trim(),
      welcome: appearance.welcome.trim(),
      welcome_description: appearance.welcome_description.trim(),
      float_icon_url: appearance.float_icon_url.trim(),
    };
    const error = validateAssistantAppearance(normalized);
    if (error) {
      setValidationError(error);
      return;
    }
    onSave(normalized);
  };

  const previewPosition: CSSProperties = {
    [appearance.float_x_anchor]:
      `${Math.min(appearance.float_x_offset || 0, 72)}px`,
    [appearance.float_y_anchor]:
      `${Math.min(appearance.float_y_offset || 0, 72)}px`,
    backgroundColor: appearance.theme,
  };

  return (
    <div className="admin-modal-backdrop" role="presentation">
      <section
        className="admin-modal admin-appearance-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-appearance-title"
      >
        <div className="admin-modal-header">
          <div>
            <p className="admin-eyebrow">APPEARANCE</p>
            <h2 id="admin-appearance-title">外观设置</h2>
            <p className="admin-appearance-app-id">
              {application.name} · {application.app_id}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭外观设置"
            disabled={saving}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="admin-appearance-layout">
          <section className="admin-appearance-preview" aria-label="静态实时预览">
            <p className="admin-appearance-section-title">静态实时预览</p>
            <div className="admin-preview-stage">
              <div className="admin-preview-chat">
                <header
                  style={{
                    backgroundColor: appearance.theme,
                    color: appearance.header_font_color,
                  }}
                >
                  {appearance.logo_url ? (
                    <img src={appearance.logo_url} alt="" />
                  ) : (
                    <span aria-hidden="true">水</span>
                  )}
                  <strong>{application.name}</strong>
                </header>
                <div className="admin-preview-chat-body">
                  <h3 style={{ color: appearance.header_font_color }}>
                    {appearance.welcome || '欢迎语'}
                  </h3>
                  <p>{appearance.welcome_description || '欢迎描述'}</p>
                  <div className="admin-preview-prompt">
                    在这里输入问题…
                  </div>
                </div>
              </div>
              <div
                className="admin-preview-float-icon"
                style={previewPosition}
                title={appearance.float_icon_draggable
                  ? '浮窗图标允许拖动'
                  : '浮窗图标固定'}
              >
                {appearance.float_icon_url ? (
                  <img src={appearance.float_icon_url} alt="" />
                ) : (
                  <span aria-hidden="true">水</span>
                )}
              </div>
            </div>
            <p className="admin-preview-note">
              预览仅渲染外观，不请求问数接口，也不签发访问 Token。
            </p>
          </section>

          <form className="admin-form admin-appearance-form" onSubmit={submit}>
            <p className="admin-appearance-section-title">配置</p>
            <div className="admin-form-grid">
              <label>
                主题色
                <span className="admin-color-field">
                  <input
                    type="color"
                    value={appearance.theme}
                    onChange={event => update('theme', event.target.value)}
                  />
                  <input
                    value={appearance.theme}
                    onChange={event => update('theme', event.target.value)}
                  />
                </span>
              </label>
              <label>
                标题文字色
                <span className="admin-color-field">
                  <input
                    type="color"
                    value={appearance.header_font_color}
                    onChange={event => update(
                      'header_font_color',
                      event.target.value,
                    )}
                  />
                  <input
                    value={appearance.header_font_color}
                    onChange={event => update(
                      'header_font_color',
                      event.target.value,
                    )}
                  />
                </span>
              </label>
              <label className="admin-form-span-2">
                Logo URL
                <input
                  type="url"
                  value={appearance.logo_url}
                  placeholder="https://example.com/logo.png"
                  onChange={event => update('logo_url', event.target.value)}
                />
              </label>
              <label className="admin-form-span-2">
                欢迎语
                <input
                  value={appearance.welcome}
                  maxLength={120}
                  onChange={event => update('welcome', event.target.value)}
                />
              </label>
              <label className="admin-form-span-2">
                欢迎描述
                <textarea
                  rows={3}
                  value={appearance.welcome_description}
                  maxLength={500}
                  onChange={event => update(
                    'welcome_description',
                    event.target.value,
                  )}
                />
              </label>
              <label className="admin-form-span-2">
                浮窗图标 URL
                <input
                  type="url"
                  value={appearance.float_icon_url}
                  placeholder="留空时使用内置图标"
                  onChange={event => update(
                    'float_icon_url',
                    event.target.value,
                  )}
                />
              </label>
              <label>
                水平锚点
                <select
                  value={appearance.float_x_anchor}
                  onChange={event => update(
                    'float_x_anchor',
                    event.target.value as AssistantAppearance[
                      'float_x_anchor'
                    ],
                  )}
                >
                  <option value="left">左侧</option>
                  <option value="right">右侧</option>
                </select>
              </label>
              <label>
                水平偏移（px）
                <input
                  type="number"
                  min={0}
                  max={1000}
                  step={1}
                  value={Number.isNaN(appearance.float_x_offset)
                    ? ''
                    : appearance.float_x_offset}
                  onChange={event => update(
                    'float_x_offset',
                    numericValue(event.target.value),
                  )}
                />
              </label>
              <label>
                垂直锚点
                <select
                  value={appearance.float_y_anchor}
                  onChange={event => update(
                    'float_y_anchor',
                    event.target.value as AssistantAppearance[
                      'float_y_anchor'
                    ],
                  )}
                >
                  <option value="top">顶部</option>
                  <option value="bottom">底部</option>
                </select>
              </label>
              <label>
                垂直偏移（px）
                <input
                  type="number"
                  min={0}
                  max={1000}
                  step={1}
                  value={Number.isNaN(appearance.float_y_offset)
                    ? ''
                    : appearance.float_y_offset}
                  onChange={event => update(
                    'float_y_offset',
                    numericValue(event.target.value),
                  )}
                />
              </label>
              <label className="admin-checkbox-row admin-form-span-2">
                <input
                  type="checkbox"
                  checked={appearance.float_icon_draggable}
                  onChange={event => update(
                    'float_icon_draggable',
                    event.target.checked,
                  )}
                />
                <span>
                  <strong>允许桌面端拖动浮窗图标</strong>
                  <small>拖动结果仅在当前页面内保留，不写入浏览器存储。</small>
                </span>
              </label>
            </div>

            {(validationError || requestError) && (
              <p className="admin-inline-error" role="alert">
                {validationError || requestError}
              </p>
            )}
            <div className="admin-modal-actions">
              <button
                className="admin-button"
                type="button"
                disabled={saving}
                onClick={() => {
                  setAppearance({ ...DEFAULT_ASSISTANT_APPEARANCE });
                  setValidationError('');
                }}
              >
                恢复默认
              </button>
              <button
                className="admin-button"
                type="button"
                disabled={saving}
                onClick={onClose}
              >
                取消
              </button>
              <button
                className="admin-button admin-button--primary"
                type="submit"
                disabled={saving}
              >
                {saving ? '正在保存…' : '保存外观'}
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}
