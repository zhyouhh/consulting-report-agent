import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('ChatPanel stream fetch 带 credentials', () => {
  const s = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')
  assert.match(s, /credentials:\s*'include'/)
})

test('IndependentReviewDrawer 两个 fetch（stream + discard）都带 credentials', () => {
  const s = readFileSync(new URL('../src/components/IndependentReviewDrawer.jsx', import.meta.url), 'utf8')
  const hits = (s.match(/credentials:\s*'include'/g) || []).length
  assert.ok(hits >= 2, `应有 ≥2 处 credentials（stream + discard），实际 ${hits}`)
})
