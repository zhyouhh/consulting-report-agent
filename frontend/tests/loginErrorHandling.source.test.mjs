import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const login = readFileSync(new URL('../src/components/Login.jsx', import.meta.url), 'utf8')
const mainJs = readFileSync(new URL('../src/main.jsx', import.meta.url), 'utf8')

// 根因（2026-06-23）：422 校验错误的数组 detail 直接 setErr → 渲染 {err} 触发 React
// "Objects are not valid as a React child" → 登录页整树卸载 → 白屏。三层防御锁死：

test('Login 用 normalizeAuthError 归一错误（不把原始 detail 直接 setErr）', () => {
  assert.match(login, /import\s*\{[^}]*normalizeAuthError[^}]*\}\s*from\s*['"][^'"]*authError/, '应导入 normalizeAuthError')
  assert.match(login, /setErr\(\s*normalizeAuthError\(/, 'catch 应 setErr(normalizeAuthError(...))')
  assert.doesNotMatch(login, /setErr\(\s*e2\?\.response\?\.data\?\.detail/, '不得把原始 detail 直接塞进 setErr（422 数组会白屏）')
})

test('Login 提交前客户端校验长度 + 提交 trim 后的用户名', () => {
  assert.match(login, /const cleanUsername\s*=\s*username\.trim\(\)/, '应取 trim 后的用户名')
  assert.match(login, /cleanUsername\.length\s*<\s*3/, '应校验用户名 ≥3（用 trim 后长度）')
  assert.match(login, /password\.length\s*<\s*6/, '应校验密码 ≥6')
  assert.match(login, /username:\s*cleanUsername/, '应提交 trim 后的用户名（避免尾随空格 + 与校验一致）')
})

test('main.jsx 用 ErrorBoundary 包住整个 App（白屏纵深防御）', () => {
  assert.match(mainJs, /import\s+ErrorBoundary\s+from\s+['"][^'"]*ErrorBoundary/, 'main.jsx 应导入 ErrorBoundary')
  assert.match(mainJs, /<ErrorBoundary>[\s\S]*<App\s*\/>[\s\S]*<\/ErrorBoundary>/, 'App 应被 ErrorBoundary 包裹')
})
