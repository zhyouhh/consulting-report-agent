import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// source-guard：renderAssistantText 是从 ChatPanel 内联正文渲染逐字抽出的，必须保持等价——
// strip 前置、splitAssistantMessageBlocks、用 block.content（非 .text）、ThinkingBlock、
// remarkGfm + assistantMarkdownComponents 都要在；防换皮时丢插件 / 丢组件映射 / 误用 .text。
const src = readFileSync(
  new URL('../src/components/assistantTextRender.jsx', import.meta.url),
  'utf8',
)

test('assistantTextRender：strip 前置 + split + block.content', () => {
  assert.match(src, /export function renderAssistantText/)
  assert.match(src, /stripToolLogComments/)
  assert.match(src, /splitAssistantMessageBlocks/)
  // 必须用 block.content（splitAssistantMessageBlocks 输出 {type, content}），不是 block.text
  assert.match(src, /block\.content/)
  assert.doesNotMatch(src, /block\.text/)
})

test('assistantTextRender：thinking → ThinkingBlock，text → ReactMarkdown(GFM+组件映射)', () => {
  assert.match(src, /<ThinkingBlock key=\{index\} text=\{block\.content\} \/>/)
  assert.match(src, /import remarkGfm from 'remark-gfm'/)
  assert.match(src, /remarkPlugins=\{\[remarkGfm\]\}/)
  assert.match(src, /assistantMarkdownComponents/)
})
