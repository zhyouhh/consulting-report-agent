import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('Sidebar account block', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/logout/)        // 登出调后端
  assert.match(s, /authUser\.username/)          // 显示用户名
  assert.match(s, /登出/)
  assert.match(s, /onLoggedOut\?\.\(\)/)         // 登出后回调清本地态（即便 POST 失败也要 fire）
  // 桌面/本地（uid==="local"）不显示账号块，避免点登出困在登录页
  assert.match(s, /authUser\.uid !== ['"]local['"]/)
})
