import { test } from 'node:test'; import assert from 'node:assert/strict'
import { mutateCurrentTextPart, applyToolEventToParts, closePendingToolParts, appendErrorPart, partsToText } from '../src/utils/messageParts.js'

const snap = x => JSON.parse(JSON.stringify(x))

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

test('mutateCurrentTextPart：不 mutate 入参，续接段是新对象引用、其余元素保留原引用', () => {
  const input = [{ type: 'tool', id: 'c1', tool: 't', arg: '', status: 'pending', summary: '' }, { type: 'text', text: '前' }]
  const before = snap(input)
  const out = mutateCurrentTextPart(input, t => t + '后')
  assert.deepEqual(input, before)              // 入参整体未被 mutate
  assert.notEqual(out, input)                  // 返回新数组引用
  assert.equal(out[0], input[0])               // 未被更新的元素保留原引用
  assert.notEqual(out[1], input[1])            // 被更新的 text 段是新对象引用
  assert.equal(out[1].text, '前后')
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

test('applyToolEventToParts：更新分支空 tool 名不清空已有工具名（|| 非空覆盖），arg 空串保留 ??', () => {
  let p = applyToolEventToParts([], { type: 'tool_call', id: 'c1', tool: 'read_file', arg: 'a.md' })
  p = applyToolEventToParts(p, { type: 'tool_call', id: 'c1', tool: '', arg: '' })
  assert.equal(p[0].tool, 'read_file')         // 空 tool 名不覆盖
  assert.equal(p[0].arg, '')                   // arg:'' 是合法值（?? 不兜底空串）
})

test('applyToolEventToParts：不 mutate 入参，更新的工具是新对象引用、其余保留原引用', () => {
  const input = [{ type: 'text', text: 'x' }, { type: 'tool', id: 'c1', tool: 'read_file', arg: '', status: 'pending', summary: '' }]
  const before = snap(input)
  const out = applyToolEventToParts(input, { type: 'tool_result', id: 'c1', tool: 'read_file', status: 'success', summary: 'ok' })
  assert.deepEqual(input, before)              // 入参整体未被 mutate
  assert.notEqual(out, input)                  // 返回新数组引用
  assert.equal(out[0], input[0])               // 未更新元素保留原引用
  assert.notEqual(out[1], input[1])            // 被更新的工具是新对象引用
  assert.equal(out[1].status, 'success')
  assert.equal(input[1].status, 'pending')     // 原对象未被原地改
})

test('applyToolEventToParts：result 先到也建 tool 片段', () => {
  assert.equal(applyToolEventToParts([], { type: 'tool_result', id: 'z', tool: 'x', status: 'error', summary: 'e' })[0].status, 'error')
})

test('closePendingToolParts：pending→error，文本/已终态不动、不可变', () => {
  const p = [{ type: 'text', text: 'a' }, { type: 'tool', id: 'c1', tool: 't', arg: '', status: 'pending', summary: '' },
             { type: 'tool', id: 'c2', tool: 't2', arg: '', status: 'success', summary: 'ok' }]
  const before = snap(p)
  const out = closePendingToolParts(p, '已停止生成')
  assert.equal(out[1].status, 'error'); assert.equal(out[1].summary, '已停止生成')
  assert.equal(out[2].status, 'success'); assert.equal(out[0].text, 'a'); assert.notEqual(out, p)
  // 强不可变：入参整体未被 mutate，原 pending 对象 status 仍是 'pending'
  assert.deepEqual(p, before)
  assert.equal(p[1].status, 'pending')         // 原数组里的 pending 对象未被原地改
  assert.notEqual(out[1], p[1])                // 被更新的工具是新对象引用
  assert.equal(out[0], p[0]); assert.equal(out[2], p[2])  // 未更新元素保留原引用
})

test('closePendingToolParts：pending 已有 summary 不被兜底覆盖', () => {
  const p = [{ type: 'tool', id: 'c1', tool: 'read_file', arg: '', status: 'pending', summary: '已读 3KB' }]
  const out = closePendingToolParts(p, '已停止生成')
  assert.equal(out[0].status, 'error')
  assert.equal(out[0].summary, '已读 3KB')      // 自身 summary 保留、不被兜底覆盖
})

test('appendErrorPart：追加报错文本段', () => {
  const p = appendErrorPart([{ type: 'tool', id: 'c1' }], '连接中断')
  assert.deepEqual(p[p.length - 1], { type: 'text', text: '连接中断' })
})

test('partsToText：拼 text 段', () => {
  assert.equal(partsToText([{ type: 'text', text: '一' }, { type: 'tool', id: 'c1' }, { type: 'text', text: '二' }]), '一二')
  assert.equal(partsToText(null), '')
})
