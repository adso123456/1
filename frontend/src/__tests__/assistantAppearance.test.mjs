import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const modulePath = path.join(
  frontendRoot,
  'src',
  'assistantAppearance.ts',
);
const appearanceModule = await import(pathToFileURL(modulePath));
const {
  DEFAULT_ASSISTANT_APPEARANCE,
  normalizeAssistantAppearance,
  validateAssistantAppearance,
} = appearanceModule;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const defaults = normalizeAssistantAppearance({});
assert(
  JSON.stringify(defaults) === JSON.stringify(DEFAULT_ASSISTANT_APPEARANCE),
  '缺失配置未回退统一默认值',
);

const valid = normalizeAssistantAppearance({
  ...DEFAULT_ASSISTANT_APPEARANCE,
  theme: '#ABCDEF',
  header_font_color: '#123456',
  float_icon_url: 'https://example.test/icon.svg',
  float_icon_draggable: true,
  float_x_anchor: 'left',
  float_x_offset: 1000,
  float_y_anchor: 'top',
  float_y_offset: 0,
});
assert(valid.theme === '#abcdef', '颜色未规范化');
assert(valid.float_x_offset === 1000, '合法边界偏移被回退');
assert(validateAssistantAppearance(valid) === null, '合法配置未通过验证');
const whiteHeaderAppearance = {
  ...DEFAULT_ASSISTANT_APPEARANCE,
  theme: '#1677ff',
  header_font_color: '#ffffff',
};
assert(
  validateAssistantAppearance(whiteHeaderAppearance) === null,
  '蓝色主题与白色 Header 文字配置被错误拒绝',
);

for (const [field, invalid] of [
  ['welcome', ''],
  ['welcome', '   '],
  ['welcome_description', ''],
  ['welcome_description', '   '],
]) {
  const error = validateAssistantAppearance({
    ...DEFAULT_ASSISTANT_APPEARANCE,
    [field]: invalid,
  });
  assert(error !== null, `${field} 空文本未被真实校验函数拒绝`);
}

for (const [field, invalid] of [
  ['theme', 'red'],
  ['header_font_color', '#12345g'],
  ['logo_url', 'javascript:alert(1)'],
  ['float_icon_url', 'https://user:pass@example.test/a.png'],
  ['float_x_anchor', 'center'],
  ['float_y_anchor', 'middle'],
  ['float_x_offset', 1001],
  ['float_y_offset', true],
]) {
  const normalized = normalizeAssistantAppearance({
    ...DEFAULT_ASSISTANT_APPEARANCE,
    [field]: invalid,
  });
  assert(
    normalized[field] === DEFAULT_ASSISTANT_APPEARANCE[field],
    `${field} 非法值未安全回退`,
  );
}

const dialogSource = fs.readFileSync(
  path.join(
    frontendRoot,
    'src',
    'components',
    'admin',
    'AssistantAppearanceDialog.tsx',
  ),
  'utf8',
);
const adminSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'AdminApp.tsx'),
  'utf8',
);
for (const forbidden of ['<iframe', 'useSSE', 'fetch(', 'localStorage', 'sessionStorage']) {
  assert(
    !dialogSource.includes(forbidden),
    `静态预览包含禁止能力: ${forbidden}`,
  );
}
assert(
  dialogSource.includes('setAppearance({ ...DEFAULT_ASSISTANT_APPEARANCE })'),
  '恢复默认未仅重置弹窗表单',
);
assert(
  dialogSource.includes('onSave(normalized)'),
  '保存未提交当前规范化外观快照',
);
const previewHeaderStyle = dialogSource.match(
  /<header\s+style=\{\{\s*([\s\S]*?)\}\}\s*>/,
);
assert(
  dialogSource.includes('style={{ borderTopColor: appearance.theme }}')
    && previewHeaderStyle?.[1].includes('color: appearance.header_font_color')
    && !previewHeaderStyle?.[1].includes('backgroundColor'),
  '静态预览仍将主题色错误用作 Header 背景',
);
assert(
  dialogSource.includes('className="admin-preview-header-actions"')
    && dialogSource.includes('className="admin-preview-status-dot"'),
  '静态预览未模拟 Widget 标题操作区或主题色状态标识',
);
const adminCssSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'AdminApp.css'),
  'utf8',
);
assert(
  /\.admin-preview-chat header\s*\{[\s\S]*?background:\s*#fff;/.test(
    adminCssSource,
  ),
  '静态预览 Header 不是白色背景',
);
assert(
  dialogSource.includes('color: appearance.header_font_color'),
  'header_font_color=#ffffff 未被保留为真实 Header 文字色',
);
assert(
  adminSource.includes('appearanceSessionRef')
    && adminSource.includes('appearanceSaveRef')
    && adminSource.includes('lifecycleEpochRef'),
  '外观保存缺少 session、请求和生命周期代次所有权',
);
assert(
  adminSource.includes('request.controller.signal')
    && adminSource.includes('appearanceDialogRef.current = null'),
  '外观请求未接入中止和关闭隔离',
);

console.log('assistant appearance: executable normalization and UI guards passed');
