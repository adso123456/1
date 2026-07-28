export type HorizontalAnchor = 'left' | 'right';
export type VerticalAnchor = 'top' | 'bottom';

export interface AssistantAppearance {
  theme: string;
  header_font_color: string;
  logo_url: string;
  welcome: string;
  welcome_description: string;
  float_icon_url: string;
  float_icon_draggable: boolean;
  float_x_anchor: HorizontalAnchor;
  float_x_offset: number;
  float_y_anchor: VerticalAnchor;
  float_y_offset: number;
}

export const DEFAULT_ASSISTANT_APPEARANCE: Readonly<AssistantAppearance> = {
  theme: '#1677ff',
  header_font_color: '#1f2329',
  logo_url: '',
  welcome: '有什么可以帮助你的？',
  welcome_description:
    '用中文自然语言提问，Agent 自动查询数据库并返回图表',
  float_icon_url: '',
  float_icon_draggable: false,
  float_x_anchor: 'right',
  float_x_offset: 24,
  float_y_anchor: 'bottom',
  float_y_offset: 24,
};

const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;
const MAX_URL_LENGTH = 2048;
const MAX_OFFSET = 1000;

export function isSafeAssetUrl(value: string): boolean {
  if (!value) return true;
  if (
    value.length > MAX_URL_LENGTH
    || value !== value.trim()
    || value.includes('<')
    || value.includes('>')
  ) return false;
  try {
    const url = new URL(value);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && !url.username
      && !url.password
    );
  } catch {
    return false;
  }
}

function isOffset(value: unknown): value is number {
  return (
    typeof value === 'number'
    && Number.isInteger(value)
    && value >= 0
    && value <= MAX_OFFSET
  );
}

function safeText(
  value: unknown,
  fallback: string,
  maximum: number,
): string {
  if (typeof value !== 'string') return fallback;
  const normalized = value.trim();
  return (
    normalized
    && normalized.length <= maximum
    && !normalized.includes('<')
    && !normalized.includes('>')
  )
    ? normalized
    : fallback;
}

export function normalizeAssistantAppearance(
  value: unknown,
): AssistantAppearance {
  const candidate = (
    typeof value === 'object' && value !== null
      ? value as Partial<AssistantAppearance>
      : {}
  );
  return {
    theme: typeof candidate.theme === 'string'
      && COLOR_PATTERN.test(candidate.theme)
      ? candidate.theme.toLowerCase()
      : DEFAULT_ASSISTANT_APPEARANCE.theme,
    header_font_color:
      typeof candidate.header_font_color === 'string'
      && COLOR_PATTERN.test(candidate.header_font_color)
        ? candidate.header_font_color.toLowerCase()
        : DEFAULT_ASSISTANT_APPEARANCE.header_font_color,
    logo_url: typeof candidate.logo_url === 'string'
      && isSafeAssetUrl(candidate.logo_url)
      ? candidate.logo_url
      : DEFAULT_ASSISTANT_APPEARANCE.logo_url,
    welcome: safeText(
      candidate.welcome,
      DEFAULT_ASSISTANT_APPEARANCE.welcome,
      120,
    ),
    welcome_description: safeText(
      candidate.welcome_description,
      DEFAULT_ASSISTANT_APPEARANCE.welcome_description,
      500,
    ),
    float_icon_url: typeof candidate.float_icon_url === 'string'
      && isSafeAssetUrl(candidate.float_icon_url)
      ? candidate.float_icon_url
      : DEFAULT_ASSISTANT_APPEARANCE.float_icon_url,
    float_icon_draggable:
      typeof candidate.float_icon_draggable === 'boolean'
        ? candidate.float_icon_draggable
        : DEFAULT_ASSISTANT_APPEARANCE.float_icon_draggable,
    float_x_anchor:
      candidate.float_x_anchor === 'left'
      || candidate.float_x_anchor === 'right'
        ? candidate.float_x_anchor
        : DEFAULT_ASSISTANT_APPEARANCE.float_x_anchor,
    float_x_offset: isOffset(candidate.float_x_offset)
      ? candidate.float_x_offset
      : DEFAULT_ASSISTANT_APPEARANCE.float_x_offset,
    float_y_anchor:
      candidate.float_y_anchor === 'top'
      || candidate.float_y_anchor === 'bottom'
        ? candidate.float_y_anchor
        : DEFAULT_ASSISTANT_APPEARANCE.float_y_anchor,
    float_y_offset: isOffset(candidate.float_y_offset)
      ? candidate.float_y_offset
      : DEFAULT_ASSISTANT_APPEARANCE.float_y_offset,
  };
}

export function validateAssistantAppearance(
  appearance: AssistantAppearance,
): string | null {
  if (!COLOR_PATTERN.test(appearance.theme)) {
    return '主题色必须使用 #RRGGBB 格式。';
  }
  if (!COLOR_PATTERN.test(appearance.header_font_color)) {
    return '标题文字色必须使用 #RRGGBB 格式。';
  }
  if (!isSafeAssetUrl(appearance.logo_url)) {
    return 'Logo URL 必须为空或为不含凭据的 http/https URL。';
  }
  if (!isSafeAssetUrl(appearance.float_icon_url)) {
    return '浮窗图标 URL 必须为空或为不含凭据的 http/https URL。';
  }
  if (safeText(appearance.welcome, '', 120) !== appearance.welcome.trim()) {
    return '欢迎语不能为空、不能包含 HTML，且不能超过 120 个字符。';
  }
  if (
    safeText(appearance.welcome_description, '', 500)
    !== appearance.welcome_description.trim()
  ) {
    return '欢迎描述不能为空、不能包含 HTML，且不能超过 500 个字符。';
  }
  if (typeof appearance.float_icon_draggable !== 'boolean') {
    return '浮窗拖动开关必须是布尔值。';
  }
  if (
    appearance.float_x_anchor !== 'left'
    && appearance.float_x_anchor !== 'right'
  ) {
    return '水平锚点只能是 left 或 right。';
  }
  if (
    appearance.float_y_anchor !== 'top'
    && appearance.float_y_anchor !== 'bottom'
  ) {
    return '垂直锚点只能是 top 或 bottom。';
  }
  if (!isOffset(appearance.float_x_offset)) {
    return '水平偏移必须是 0 到 1000 的整数。';
  }
  if (!isOffset(appearance.float_y_offset)) {
    return '垂直偏移必须是 0 到 1000 的整数。';
  }
  return null;
}
