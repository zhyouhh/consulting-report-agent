import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  appendErrorPart,
  applyToolEventToParts,
  closePendingToolParts,
  mutateCurrentTextPart,
  partsToText,
} from '../src/utils/messageParts.js'
import { appendThinkingEventContent } from '../src/utils/chatPresentation.js'

const src = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')

// ── source-guard：每个写 content 的 handler 旁建了 parts、reload 映射、复制经 strip ──
test('ChatPanel：旁建 parts 各算子 + reload + 复制经 strip', () => {
  // thinking / 诊断 tool 分支用 m 变量并入当前 text 片段
  assert.match(src, /mutateCurrentTextPart\(m\.parts/)
  assert.match(src, /appendThinkingEventContent/)
  // 结构化工具事件旁建有序片段
  assert.match(src, /applyToolEventToParts\(m\.parts/)
  // 三处异常收尾仍 pending 的工具片段
  assert.match(src, /closePendingToolParts\(/)
  // reload 映射保留后端有序 parts
  assert.match(src, /parts:\s*m\.parts/)
  // 复制源在 parts 非空时用 partsToText，仍过现有 strip helper
  assert.match(src, /(getCopyableAssistantMessageText|stripToolLogComments)\([^)]*partsToText/)
})

test('ChatPanel：两个 flush 点旁建 parts（content 写入不变）', () => {
  // 即时冲刷：本次 pending 段同时进 content 与 parts
  assert.match(src, /content:\s*message\.content \+ pending,\s*parts:\s*mutateCurrentTextPart\(message\.parts \|\| \[\],\s*t => t \+ pending\)/)
  // 定时切片冲刷：本次 emitted 段同时进 content 与 parts
  assert.match(src, /content:\s*message\.content \+ emitted,\s*parts:\s*mutateCurrentTextPart\(message\.parts \|\| \[\],\s*t => t \+ emitted\)/)
})

test('ChatPanel：tool_call/tool_result 旁建 parts 用 applyToolEventToParts(parsed)', () => {
  assert.match(src, /toolEvents:\s*reduceToolEvent\(m\.toolEvents \|\| \[\], parsed\),\s*parts:\s*applyToolEventToParts\(m\.parts \|\| \[\], parsed\)/)
})

// ── IP6：渲染切到 parts 穿插（MessageParts），无 parts 回落到旧分组（ToolCallList + 正文）──
test('ChatPanel：parts 有则 MessageParts、否则 ToolCallList + renderAssistantText fallback', () => {
  assert.match(src, /import MessageParts from '\.\/MessageParts'/)
  assert.match(src, /import \{ renderAssistantText \} from '\.\/assistantTextRender'/)
  assert.match(src, /msg\.parts && msg\.parts\.length/)
  assert.match(src, /<MessageParts parts=\{msg\.parts\} onOpenFile=\{onOpenWorkspaceFile\} \/>/)
  // fallback 老消息：ToolCallList 堆顶 + renderAssistantText(msg.content, …)（与抽取前等价，
  // 文件内链批次起带 onOpenFile options）
  assert.match(src, /renderAssistantText\(msg\.content, \{ onOpenFile: onOpenWorkspaceFile \}\)/)
})

// ── 行为锁测：用真实纯函数模拟 ChatPanel 的 per-event parts 归约，断言时间线穿插正确 ──
test('行为①：pending content flush 后再 tool_call → parts 为 text → tool', () => {
  let parts = []
  // tool_call 前会先 flushStreamingQueueImmediately 把待发文本冲出 → 进当前 text 段
  parts = mutateCurrentTextPart(parts, t => t + '工具前的正文')
  parts = applyToolEventToParts(parts, { type: 'tool_call', id: 't1', tool: 'web_search', arg: '关键词' })

  assert.equal(parts.length, 2)
  assert.deepEqual(parts[0], { type: 'text', text: '工具前的正文' })
  assert.equal(parts[1].type, 'tool')
  assert.equal(parts[1].id, 't1')
  assert.equal(parts[1].status, 'pending')
})

test('行为②：thinking → tool_call/result → thinking → parts 为 text(含thinking) → tool → text(含thinking)', () => {
  let parts = []
  parts = mutateCurrentTextPart(parts, t => appendThinkingEventContent(t, '先想'))
  parts = applyToolEventToParts(parts, { type: 'tool_call', id: 't1', tool: 'fetch_url', arg: 'https://x' })
  parts = applyToolEventToParts(parts, { type: 'tool_result', id: 't1', status: 'success', summary: '已抓取' })
  parts = mutateCurrentTextPart(parts, t => appendThinkingEventContent(t, '再想'))

  assert.equal(parts.length, 3)
  assert.equal(parts[0].type, 'text')
  assert.match(parts[0].text, /<thinking-block>先想<\/thinking-block>/)
  assert.equal(parts[1].type, 'tool')
  assert.equal(parts[1].status, 'success')
  assert.equal(parts[1].summary, '已抓取')
  assert.equal(parts[2].type, 'text')
  assert.match(parts[2].text, /<thinking-block>再想<\/thinking-block>/)
})

test('行为③：in-stream error → 末段 text 为「错误: ...」、pending 工具→error', () => {
  let parts = []
  parts = applyToolEventToParts(parts, { type: 'tool_call', id: 't1', tool: 'web_search', arg: 'q' })
  // 错误 handler：先 appendErrorPart(displayText) 再 closePendingToolParts(pendingSummary)
  parts = closePendingToolParts(appendErrorPart(parts, '错误: 上游超时'), '生成出错')

  assert.equal(parts.length, 2)
  assert.equal(parts[0].type, 'tool')
  assert.equal(parts[0].status, 'error')
  assert.equal(parts[0].summary, '生成出错')
  assert.deepEqual(parts[1], { type: 'text', text: '错误: 上游超时' })
})

test('行为④：abort 有可见文本时不追加「已停止生成」', () => {
  let parts = mutateCurrentTextPart([], t => t + '已经写了一段')
  parts = applyToolEventToParts(parts, { type: 'tool_call', id: 't1', tool: 'x', arg: '' })
  // abort：displayText 仅在无可见文本时才为「已停止生成」
  const displayText = (partsToText(parts) || '') ? '' : '已停止生成'
  parts = closePendingToolParts(appendErrorPart(parts, displayText), '已停止生成')

  // 末尾仍是工具片段（无新增文本），工具被收尾为 error
  assert.equal(parts.length, 2)
  assert.equal(parts[0].text, '已经写了一段')
  assert.equal(parts[1].type, 'tool')
  assert.equal(parts[1].status, 'error')
  assert.equal(parts[1].summary, '已停止生成')
})
