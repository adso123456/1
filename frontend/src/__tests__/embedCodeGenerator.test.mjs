import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.resolve(
  currentDir,
  '..',
  'embedCodeGenerator.ts',
);
const source = fs.readFileSync(sourcePath, 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const module = { exports: {} };
vm.runInNewContext(javascript, {
  module,
  exports: module.exports,
  URL,
  Error,
  Set,
});
const { generateEmbedCode, normalizeHttpOrigin } = module.exports;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(
  normalizeHttpOrigin('https://agent.example.com')
    === 'https://agent.example.com',
  '合法 Origin 未通过',
);
for (const invalid of [
  'https://agent.example.com/path',
  'https://agent.example.com/?x=1',
  'https://agent.example.com/#x',
  'https://*.example.com',
  'ftp://agent.example.com',
]) {
  assert(normalizeHttpOrigin(invalid) === '', `非法 Origin 未拒绝: ${invalid}`);
}

const output = generateEmbedCode({
  appId: 'safe-app',
  parentOrigin: 'https://host.example.com',
  allowedSourceIds: ['source-a', 'source-"quoted'],
  tokenTtlSeconds: 300,
  agentOrigin: 'https://agent.example.com',
});
assert(
  output.browserHtml.includes('data-auto-init="false"')
    && output.browserHtml.includes('/api/water-agent/embed-token')
    && output.browserHtml.includes('credentials: "same-origin"'),
  '浏览器模板未使用宿主同源 Token 接口',
);
assert(
  !output.browserHtml.includes('safe-app')
    && !output.browserHtml.includes('source-a')
    && !output.browserHtml.includes('host.example.com'),
  '浏览器模板暴露了可修改权限参数',
);
assert(
  output.pythonFastApi.includes('require_current_user')
    && output.pythonFastApi.includes('current_user.id')
    && output.pythonFastApi.includes('"aud": "water-agent-embed"')
    && output.pythonFastApi.includes('algorithm="HS256"')
    && output.pythonFastApi.includes('Cache-Control'),
  'FastAPI 模板缺少认证、固定 claims 或禁缓存',
);
assert(
  output.environment.includes('WATER_AGENT_APP_ID=safe-app')
    && output.environment.includes(
      'WATER_AGENT_APP_SECRET=<paste_saved_application_secret_here>',
    ),
  '环境变量模板未使用一次性 Secret 占位符',
);
const combined = Object.values(output).join('\n');
for (const forbidden of [
  '/api/admin',
  'water-agent-admin-preview',
  'project-admin-preview-v1',
  'real-secret-value',
  'administrator-token',
]) {
  assert(!combined.includes(forbidden), `生成代码包含禁止内容: ${forbidden}`);
}
assert(
  output.pythonFastApi.includes('"source-\\"quoted"'),
  'Python 动态字符串未安全转义',
);

console.log('embed code generator: all checks passed');
