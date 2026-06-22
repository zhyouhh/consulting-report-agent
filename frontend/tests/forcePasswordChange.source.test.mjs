import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('ForcePasswordChange 调改密端点', () => {
  const s = readFileSync(new URL('../src/components/ForcePasswordChange.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/change-password/)
})

test('App 在 must_change_password 时挂强制改密屏', () => {
  const s = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(s, /must_change_password/)
  assert.match(s, /ForcePasswordChange/)
})
