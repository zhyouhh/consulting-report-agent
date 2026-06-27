import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ThinkingBlock from './ThinkingBlock'
// Shared markdown rendering fragment, reused by the S5 ReviewChatWindow (same look & feel).
import { assistantMarkdownComponents } from './MarkdownMessage'
import { stripToolLogComments } from '../utils/toolLogStrip.mjs'
import { splitAssistantMessageBlocks } from '../utils/chatPresentation'

// 把 assistant 正文文本渲染为有序 block 元素数组（thinking → ThinkingBlock，text → ReactMarkdown）。
// 从 ChatPanel 内联渲染逐字抽出，保持等价：先 stripToolLogComments（stage-ack strip 由
// splitAssistantMessageBlocks 内部 stripStageAckTags 处理）→ splitAssistantMessageBlocks → 逐 block
// 渲染（用 block.content 字段，不是 .text）。返回数组（带 key），可直接放进 JSX。
export function renderAssistantText(text) {
  const clean = stripToolLogComments(text || '')
  const blocks = splitAssistantMessageBlocks(clean)
  return blocks.map((block, index) => block.type === 'thinking' ? (
    <ThinkingBlock key={index} text={block.content} />
  ) : (
    <ReactMarkdown
      key={index}
      className="max-w-none"
      remarkPlugins={[remarkGfm]}
      components={assistantMarkdownComponents}
    >
      {block.content}
    </ReactMarkdown>
  ))
}
