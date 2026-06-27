import { test } from 'node:test'; import assert from 'node:assert/strict'
import { mutateCurrentTextPart, applyToolEventToParts, closePendingToolParts, appendErrorPart, partsToText } from '../src/utils/messageParts.js'

test('mutateCurrentTextPart：末尾是 text 续接，否则新建', () => {
  let p = mutateCurrentTextPart([], t => t + '你好')
  assert.deepEqual(p, [{ type: 'text', text: '你好' }])
  p = mutateCurrentTextPart(p, t => t + '世界')
  assert.deepEqual(p, [{ type: 'text', text: '你好世界' }])
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  p = mutateCurrentTextPart(p, t => t + '改完')   // 末尾是 tool → 新 text 段
  assert.deepEqual(p.map(x => x.type), ['text', 'tool', 'text'])
  assert.equal(p[2].text, '改完')
})

test('applyToolEventToParts：早发 pending→同 id full arg 原地更新、不产两个；result 按 id 收尾', () => {
  let p = mutateCurrentTextPart([], () => '准备')
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: '' })
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  p = applyToolEventToParts(p, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: '' })
  const tools = p.filter(x => x.type === 'tool')
  assert.equal(tools.length, 1)
  assert.deepEqual(tools[0], { type: 'tool', id: 'c1', tool: 'read_file', arg: 'a.md', status: 'success', summary: '' })
})

test('applyToolEventToParts：result 先到也建 tool 片段', () => {
  assert.equal(applyToolEventToParts([], { type: 'tool_result', id: 'z', tool: 'x', status: 'error', summary: 'e' })[0].status, 'error')
})

test('closePendingToolParts：pending→error，文本/已终态不动、不可变', () => {
  const p = [{ type: 'text', text: 'a' }, { type: 'tool', id: 'c1', tool: 't', arg: '', status: 'pending', summary: '' },
             { type: 'tool', id: 'c2', tool: 't2', arg: '', status: 'success', summary: 'ok' }]
  const out = closePendingToolParts(p, '已停止生成')
  assert.equal(out[1].status, 'error'); assert.equal(out[1].summary, '已停止生成')
  assert.equal(out[2].status, 'success'); assert.equal(out[0].text, 'a'); assert.notEqual(out, p)
})

test('appendErrorPart：追加报错文本段', () => {
  const p = appendErrorPart([{ type: 'tool', id: 'c1' }], '连接中断')
  assert.deepEqual(p[p.length - 1], { type: 'text', text: '连接中断' })
})

test('partsToText：拼 text 段', () => {
  assert.equal(partsToText([{ type: 'text', text: '一' }, { type: 'tool', id: 'c1' }, { type: 'text', text: '二' }]), '一二')
  assert.equal(partsToText(null), '')
})
