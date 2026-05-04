import { test } from 'node:test'
import assert from 'node:assert/strict'
import { stripToolLogComments } from '../src/utils/toolLogStrip.mjs'

test('strips well-formed comment', () => {
  const s = 'Reply.\n<!-- tool-log\n- web_search ✓\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('strips multi-line', () => {
  const s = 'Reply.\n<!-- tool-log\n- a ✓\n- b ✗ err\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('handles unclosed truncated stream', () => {
  const s = 'Reply.\n<!-- tool-log\n- partial ✓'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('handles nested -- inside comment', () => {
  const s = 'Reply.\n<!-- tool-log\n- some -- tool ✓\n-->'
  assert.equal(stripToolLogComments(s), 'Reply.')
})

test('preserves non-tool-log comments', () => {
  const s = 'Reply.\n<!-- regular html comment -->'
  assert.equal(stripToolLogComments(s), s)
})
