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
  // lock the three status branches (deleting any would fail)
  assert.match(src, /status\s*===\s*['"]success['"]/)
  assert.match(src, /status\s*===\s*['"]error['"]/)
  assert.match(src, /text-success/)
  assert.match(src, /text-error/)
  // no dark: prefix (dark theme is driven by tokens)
  assert.doesNotMatch(src, /\bdark:/)
  // trust boundary: plain-text rendering only
  assert.doesNotMatch(src, /dangerouslySetInnerHTML/)
  // a11y: sr-only status text + real button (keyboard-operable expand)
  assert.match(src, /sr-only/)
  assert.match(src, /type="button"|type:\s*['"]button['"]/)
  assert.match(src, /aria-expanded/)
})
