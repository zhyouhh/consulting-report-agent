import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/AdminPanel.jsx', import.meta.url), 'utf8')

test('AdminPanel 调 admin 端点 + 复用 adminApi', () => {
  assert.match(src, /\/api\/admin\/users/)
  assert.match(src, /\/api\/admin\/users\/\$\{[^}]+\}\/(password|cap|disabled)/)
  assert.match(src, /\/api\/admin\/invite-code\/rotate/)
  assert.match(src, /\/api\/admin\/allowed-hosts/)
  assert.match(src, /from '\.\.\/utils\/adminApi'/)
})
