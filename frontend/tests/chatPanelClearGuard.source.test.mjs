import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')

test('清空对话 handler 在 loading/uploading 时早返（生成中禁清空）', () => {
  const fn = src.slice(src.indexOf('const clearConversation'), src.indexOf('const clearConversation') + 200)
  // 后端端点已离 loop，前端再加一道：避免与持锁的聊天轮竞争（W2-C 终审 BLOCKER 防御）
  assert.match(fn, /if\s*\(\s*loading\s*\|\|\s*uploading\s*\)\s*return/, 'clearConversation 必须在 loading||uploading 时早返')
})

test('清空对话 按钮在 loading/uploading 时 disabled', () => {
  // 锚定按钮的 onClick（'清空对话' 字样首次出现在 confirm 字符串里，不能用它定位）
  const onClickIdx = src.indexOf('onClick={clearConversation}')
  assert.ok(onClickIdx > -1, '必须有 onClick={clearConversation} 的清空按钮')
  const start = src.lastIndexOf('<button', onClickIdx)
  const labelIdx = src.indexOf('清空对话', onClickIdx)  // 按钮标签（在 onClick 之后）
  const btn = src.slice(start, labelIdx)
  assert.match(btn, /disabled=\{\s*loading\s*\|\|\s*uploading\s*\}/, '清空对话按钮必须 disabled={loading || uploading}')
})
