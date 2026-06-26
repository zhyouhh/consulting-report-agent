import { test } from 'node:test'; import assert from 'node:assert/strict'
import { reduceToolEvent, firstArgValue, closePendingToolEvents } from '../src/utils/toolEvents.js'
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
test('reduceToolEvent：result 先到、call 后到——status 不被退回 pending、arg 补齐', () => {
  let m = reduceToolEvent([], { type: 'tool_result', id: 'z', tool: 'read_file', status: 'error', summary: 'b' })
  m = reduceToolEvent(m, { type: 'tool_call', id: 'z', tool: 'read_file', arg: 'a.md' })
  assert.deepEqual(m, [{ id: 'z', tool: 'read_file', arg: 'a.md', status: 'error', summary: 'b' }])
})
test('firstArgValue：取首值截断', () => {
  assert.equal(firstArgValue({ file_path: 'a.md' }), 'a.md')
  assert.equal(firstArgValue({}), '')
  assert.equal(firstArgValue({ q: 'x'.repeat(50) }).length, 40)
})
test('firstArgValue：数组守卫返回空串', () => {
  assert.equal(firstArgValue(['a.md', 'b.md']), '')
})
test('closePendingToolEvents：pending → error，summary 用兜底文案', () => {
  const out = closePendingToolEvents([{ id: 'c1', tool: 'read_file', arg: 'a.md', status: 'pending', summary: '' }], '已停止生成')
  assert.deepEqual(out, [{ id: 'c1', tool: 'read_file', arg: 'a.md', status: 'error', summary: '已停止生成' }])
})
test('closePendingToolEvents：pending 自带 summary 时保留原 summary', () => {
  const out = closePendingToolEvents([{ id: 'c1', tool: 'x', arg: '', status: 'pending', summary: '已有摘要' }], '连接中断')
  assert.equal(out[0].status, 'error'); assert.equal(out[0].summary, '已有摘要')
})
test('closePendingToolEvents：已 success / error 不动', () => {
  const input = [
    { id: 'a', tool: 'x', arg: '', status: 'success', summary: 'ok' },
    { id: 'b', tool: 'y', arg: '', status: 'error', summary: 'bad' },
    { id: 'c', tool: 'z', arg: '', status: 'pending', summary: '' },
  ]
  const out = closePendingToolEvents(input, '已停止生成')
  assert.deepEqual(out.map(e => [e.id, e.status, e.summary]), [
    ['a', 'success', 'ok'], ['b', 'error', 'bad'], ['c', 'error', '已停止生成'],
  ])
})
test('closePendingToolEvents：空数组 / undefined 安全返回原值', () => {
  const empty = []
  assert.equal(closePendingToolEvents(empty, 's'), empty)
  assert.equal(closePendingToolEvents(undefined, 's'), undefined)
  assert.equal(closePendingToolEvents(null, 's'), null)
})
test('closePendingToolEvents：不可变（不改入参）', () => {
  const input = [{ id: 'c', tool: 'z', arg: '', status: 'pending', summary: '' }]
  const snapshot = JSON.stringify(input)
  const out = closePendingToolEvents(input, '已停止生成')
  assert.equal(JSON.stringify(input), snapshot)   // 入参未被改
  assert.notEqual(out, input)                       // 返回新数组
  assert.notEqual(out[0], input[0])                 // pending 项是新对象
})
