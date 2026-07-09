import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ThinkingBlock from './ThinkingBlock'
// Shared markdown rendering fragment, reused by the S5 ReviewChatWindow (same look & feel).
import { assistantMarkdownComponents } from './MarkdownMessage'
import { stripToolLogComments } from '../utils/toolLogStrip.mjs'
import { splitAssistantMessageBlocks } from '../utils/chatPresentation'
import { resolveWorkspaceFileLink } from '../utils/workspaceFileLinks'

// 文件内链（2026-07-09 试用反馈③）：助手正文里反引号提到的已知工作区文件名
// （`outline.md` / `plan/outline.md` 等，白名单精确匹配）渲染成可点击链接，直达文件 tab。
// 匹配不上的 inline code 原样走共享样式；块级 code 不参与。
function buildFileLinkComponents(onOpenFile) {
  const baseCode = assistantMarkdownComponents.code
  return {
    ...assistantMarkdownComponents,
    code: ({ node, inline, children, ...props }) => {
      const isInline = inline ?? (node?.position?.start?.line === node?.position?.end?.line)
      if (isInline) {
        const raw = Array.isArray(children) ? children.join('') : String(children ?? '')
        const linkPath = resolveWorkspaceFileLink(raw)
        if (linkPath) {
          return (
            <button
              type="button"
              onClick={() => onOpenFile(linkPath)}
              className="rounded bg-asoft px-1.5 py-0.5 font-mono text-13 text-abright underline underline-offset-2 cursor-pointer"
              title="在文件栏打开"
            >
              {raw}
            </button>
          )
        }
      }
      return baseCode({ node, inline, children, ...props })
    },
  }
}

// 把 assistant 正文文本渲染为有序 block 元素数组（thinking → ThinkingBlock，text → ReactMarkdown）。
// 从 ChatPanel 内联渲染逐字抽出，保持等价：先 stripToolLogComments（stage-ack strip 由
// splitAssistantMessageBlocks 内部 stripStageAckTags 处理）→ splitAssistantMessageBlocks → 逐 block
// 渲染（用 block.content 字段，不是 .text）。返回数组（带 key），可直接放进 JSX。
// options.onOpenFile 提供时，正文反引号文件名渲染为文件内链；缺省渲染与原先逐字一致。
export function renderAssistantText(text, options = {}) {
  const { onOpenFile } = options
  const clean = stripToolLogComments(text || '')
  const blocks = splitAssistantMessageBlocks(clean)
  const components = onOpenFile ? buildFileLinkComponents(onOpenFile) : assistantMarkdownComponents
  return blocks.map((block, index) => block.type === 'thinking' ? (
    <ThinkingBlock key={index} text={block.content} />
  ) : (
    <ReactMarkdown
      key={index}
      className="max-w-none"
      remarkPlugins={[remarkGfm]}
      components={components}
    >
      {block.content}
    </ReactMarkdown>
  ))
}
