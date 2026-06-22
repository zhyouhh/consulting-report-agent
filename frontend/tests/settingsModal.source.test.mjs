import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('SettingsModal custom 模式输入仍在 + 不再提交 managed_base_url', () => {
  const s = readFileSync(new URL('../src/components/SettingsModal.jsx', import.meta.url), 'utf8')
  assert.match(s, /custom_api_base/)
  assert.match(s, /custom_api_key/)
  assert.doesNotMatch(s, /managed_base_url:/)   // 不再把 managed_base_url 放进提交体
})
