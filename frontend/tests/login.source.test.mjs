import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('Login posts auth endpoints', () => {
  const s = readFileSync(new URL('../src/components/Login.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/login/); assert.match(s, /\/api\/auth\/register/); assert.match(s, /invite_code/)
})
test('App gates on /api/auth/me', () => {
  const s = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')
  assert.match(s, /\/api\/auth\/me/); assert.match(s, /Login/); assert.match(s, /setUnauthedHandler/)
})
