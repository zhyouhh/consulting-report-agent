import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// source-guard：锁 MessageParts 的内部映射（spec IP6 #4）。现有迁移 guard 只读 ChatPanel
// 的 import/use/fallback，没有任何测试读 MessageParts.jsx——若 tool→ToolCallPill 或
// text→renderAssistantText 映射被改坏，那些 guard 会全绿漏过。这里实读 MessageParts.jsx 锁三点。
const src = readFileSync(
  new URL('../src/components/MessageParts.jsx', import.meta.url),
  'utf8',
)

test('MessageParts：按 type 分派，tool→ToolCallPill(event=p)、text→renderAssistantText(p.text)、空守卫', () => {
  // 按片段 type 分派
  assert.match(src, /p\.type === 'tool'/)
  // tool 段 → ToolCallPill 传 event={p} + 文件内链回调透传
  assert.match(src, /ToolCallPill\s+event=\{p\}\s+onOpenFile=\{onOpenFile\}/)
  // text 段 → renderAssistantText(p.text, { onOpenFile })（文件内链批次起带 options）
  assert.match(src, /renderAssistantText\(p\.text, \{ onOpenFile \}\)/)
  // 空 parts 守卫返回 null
  assert.match(src, /!parts \|\| !parts\.length/)
})

test('MessageParts：tool key 用索引前缀防重复 id 碰撞', () => {
  // 索引前缀 + id 后缀（病态 reload 重复 tool id 不产生重复 React key）
  assert.match(src, /key=\{`\$\{i\}-\$\{p\.id \|\| 'tool'\}`\}/)
})
