import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/ToolCallPill.jsx', import.meta.url), 'utf8')

test('ToolCallPill: single-line + status icon + summary click-to-expand, all tokens, no emoji', () => {
  assert.match(src, /inline-flex/)
  assert.match(src, /IconTool/)
  assert.match(src, /IconCheck/)
  assert.match(src, /IconClose/)
  assert.match(src, /status\s*===\s*['"]pending['"]/)
  assert.match(src, /font-mono/)
  assert.match(src, /min-w-0/)
  assert.match(src, /useState/)
  assert.match(src, /summary/)
  assert.match(src, /event\.tool/)
  assert.match(src, /event\.arg/)
  assert.doesNotMatch(src, /#[0-9a-fA-F]{3,6}\b/)
  assert.doesNotMatch(src, /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u)
})
