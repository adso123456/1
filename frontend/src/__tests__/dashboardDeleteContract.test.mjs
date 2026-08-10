import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(currentDir, '..', '..');
const hookSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'hooks', 'useDashboard.ts'),
  'utf8',
);
const panelSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'components', 'DashboardListPanel.tsx'),
  'utf8',
);
const appSource = fs.readFileSync(
  path.join(frontendRoot, 'src', 'App.tsx'),
  'utf8',
);

const checks = [
  ['删除能力', hookSource.includes('const deleteDashboard = useCallback')],
  ['删除当前项后切换相邻项', hookSource.includes('remaining[Math.min(index, remaining.length - 1)]?.id')],
  ['允许删除最后一个', hookSource.includes("?.id ?? ''")],
  ['删除前二次确认', panelSource.includes('确定删除仪表板')],
  ['删除按钮阻止误切换', panelSource.includes('event.stopPropagation()')],
  ['删除失败提示', panelSource.includes('删除失败，浏览器存储可能不可用')],
  ['应用接入删除回调', appSource.includes('onDelete={deleteDashboard}')],
];

for (const [name, passed] of checks) {
  if (!passed) throw new Error(`[FAIL] ${name}`);
  console.log(`[PASS] ${name}`);
}
