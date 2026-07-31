import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const testDir = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(testDir, '..');
const projectRoot = path.resolve(sourceRoot, '..', '..');
const read = (...parts) => fs.readFileSync(path.join(...parts), 'utf8');

const adminSource = read(sourceRoot, 'AdminApp.tsx');
const adminApiSource = read(sourceRoot, 'adminApi.ts');
const adminTypesSource = read(sourceRoot, 'adminTypes.ts');
const adminCssSource = read(sourceRoot, 'AdminApp.css');
const widgetSource = read(sourceRoot, 'WidgetApp.tsx');
const serverSource = read(projectRoot, 'step4_server.py');

assert(
  adminTypesSource.includes('export interface AssistantApplicationLink')
    && adminTypesSource.includes('application_links: AssistantApplicationLink[]'),
  '管理端类型缺少独立关联网站入口',
);
for (const field of [
  'link_id',
  'name',
  'url',
  'open_mode',
  'enabled',
  'sort_order',
]) {
  assert(adminTypesSource.includes(`${field}:`), `入口类型缺少 ${field}`);
}
assert(
  adminSource.includes('关联网站入口')
    && adminSource.includes('添加入口')
    && adminSource.includes('上移入口')
    && adminSource.includes('下移入口')
    && adminSource.includes('配置网站入口'),
  '新建和编辑表单缺少入口增删、排序或空状态',
);
assert(
  adminSource.includes("value=\"new_tab\"")
    && adminSource.includes("value=\"same_tab\"")
    && adminSource.includes('checked={link.enabled}'),
  '入口表单缺少打开方式或启停控制',
);
assert(
  adminSource.includes("target={")
    && adminSource.includes("'_blank'")
    && adminSource.includes("'noopener noreferrer'")
    && adminSource.includes('enabledLinks.length === 1')
    && adminSource.includes('role="menu"'),
  '单入口直达、多入口菜单或新标签页安全属性缺失',
);
assert(
  adminSource.includes('isSafeApplicationUrl')
    && adminSource.includes("url.protocol === 'http:'")
    && adminSource.includes("url.protocol === 'https:'")
    && adminSource.includes('!url.username')
    && adminSource.includes('!url.password')
    && adminSource.includes("decoded.includes('<')"),
  '前端 URL 安全校验不完整',
);
assert(
  adminApiSource.includes("method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'")
    && adminApiSource.includes('deleteApplication(appId: string'),
  '管理 API 客户端缺少应用删除',
);
assert(
  adminCssSource.includes('.admin-link-editor')
    && adminCssSource.includes('@media (max-width: 640px)')
    && adminCssSource.includes('.admin-action-menu'),
  '入口编辑区、菜单或移动端样式缺失',
);
assert(
  !widgetSource.includes('application_links')
    && !serverSource.match(
      /get_embed_application[\s\S]{0,2000}application_links/,
    ),
  '管理用入口被暴露给嵌入 Widget',
);
assert(
  adminSource.includes('allowed_origins: normalizeOrigins(form.origins)')
    && adminSource.includes('application_links: form.links.map'),
  '关联入口错误复用了 Origin 字段',
);

console.log('assistant application links frontend contract: all checks passed');
