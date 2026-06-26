import { test } from 'node:test'; import assert from 'node:assert/strict'
import { reduceToolEvent, firstArgValue } from '../src/utils/toolEvents.js'
test('reduceToolEvent：call 建 pending，result 按 id 更新且不丢 arg、保序', () => {
  let m = reduceToolEvent([], { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  assert.deepEqual(m, [{ id: 'c1', tool: 'read_file', arg: 'a.md', status: 'pending', summary: '' }])
  m = reduceToolEvent(m, { type: 'tool_call', id: 'c2', tool: 'web_search', arg: 'q' })
  m = reduceToolEvent(m, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: 'ok' })
  assert.deepEqual(m.map(e => [e.id, e.status]), [['c1','success'],['c2','pending']]); assert.equal(m[0].arg, 'a.md')
})
test('reduceToolEvent：result 先到也建条目', () => {
  assert.equal(reduceToolEvent([], { type: 'tool_result', id: 'z', tool: 'x', status: 'error', summary: 'b' })[0].status, 'error')
})
test('firstArgValue：取首值截断', () => {
  assert.equal(firstArgValue({ file_path: 'a.md' }), 'a.md')
  assert.equal(firstArgValue({}), '')
  assert.equal(firstArgValue({ q: 'x'.repeat(50) }).length, 40)
})
