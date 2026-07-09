import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ThinkingBlock from './ThinkingBlock'
// Shared markdown rendering fragment, reused by the S5 ReviewChatWindow (same look & feel).
import { assistantMarkdownComponents } from './MarkdownMessage'
import { stripToolLogComments } from '../utils/toolLogStrip.mjs'
import { splitAssistantMessageBlocks } from '../utils/chatPresentation'
import { resolveWorkspaceFileLink } from '../utils/workspaceFileLinks'

// 提取 hast 节点的纯文本（value 叶子拼接），用于判断锚点内容是否是已知文件名。
function hastTextOf(node) {
  if (!node) return ''
  if (typeof node.value === 'string') return node.value
  return (node.children || []).map(hastTextOf).join('')
}

// 锚点子树里是否存在解析命中的 code 节点——必须递归到任意深度：
// [**`outline.md`**](url) 的 code 嵌在 strong 里、[`a.md` 和 `outline.md`](url) 命中的
// 不是首个 code 子节点，只查直接子级都会漏（codex 红队三轮 BLOCKER）。
function hasResolvableCodeDescendant(node) {
  if (!node) return false
  if (node.tagName === 'code' && resolveWorkspaceFileLink(hastTextOf(node))) return true
  return (node.children || []).some(hasResolvableCodeDescendant)
}

// 文件内链（2026-07-09 试用反馈③）：助手正文里反引号提到的已知工作区文件名
// （`outline.md` / `plan/outline.md` 等，白名单精确匹配）渲染成可点击链接，直达文件 tab。
// 匹配不上的 inline code 原样走共享样式；块级 code 不参与。
function buildFileLinkComponents(onOpenFile) {
  const baseCode = assistantMarkdownComponents.code
  const baseAnchor = assistantMarkdownComponents.a
  return {
    ...assistantMarkdownComponents,
    // [`outline.md`](url)：内层 code 会渲染成文件链接按钮，但外层 <a> 对键盘用户仍可
    // Tab+Enter 直接触发导航（事件目标是 <a>，子按钮的 preventDefault 拦不到）——含解析
    // 命中的 code 子节点时解包掉锚点，只留内层按钮（codex 红队 BLOCKER 二轮）。
    // 纯文本锚点 [outline.md](url)（无反引号）不解包：那是模型给的外部链接，保持原样。
    a: ({ node, children, ...props }) => {
      if (hasResolvableCodeDescendant(node)) {
        return <>{children}</>
      }
      return baseAnchor({ children, ...props })
    },
    code: ({ node, inline, children, ...props }) => {
      const isInline = inline ?? (node?.position?.start?.line === node?.position?.end?.line)
      if (isInline) {
        const raw = Array.isArray(children) ? children.join('') : String(children ?? '')
        const linkPath = resolveWorkspaceFileLink(raw)
        if (linkPath) {
          return (
            <button
              type="button"
              // preventDefault + stopPropagation：文件名可能被模型写在 markdown 链接里
              //（[`outline.md`](url) → 按钮嵌在 <a> 内），不拦会同时触发锚点导航把整个
              // SPA 导走、丢内存态（codex 整分支审 BLOCKER）。
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); onOpenFile(linkPath) }}
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
