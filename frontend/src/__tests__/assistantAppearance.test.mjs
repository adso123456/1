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
assert(
  adminSource.includes('appearanceSessionRef')
    && adminSource.includes('appearanceSaveRef')
    && adminSource.includes('authEpochRef'),
  '外观保存缺少 session、请求和认证代次所有权',
);
assert(
  adminSource.includes('request.controller.signal')
    && adminSource.includes('appearanceDialogRef.current = null'),
  '外观请求未接入中止和关闭隔离',
);

console.log('assistant appearance: executable normalization and UI guards passed');
