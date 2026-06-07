import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Shared markdown rendering for assistant prose, reused by the main ChatPanel and the S5
// ReviewChatWindow so both render identically (GFM tables, dark-theme cells). The content is
// expected to be ALREADY backend-stripped of <think> blocks — do NOT re-strip in the frontend.
export const assistantMarkdownComponents = {
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">
        {children}
      </table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-[#3a3a5a] bg-[#1a1a2e] px-2 py-1 text-left font-semibold text-[#e2e2f0]">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-[#3a3a5a] px-2 py-1 align-top text-[#e2e2f0]">
      {children}
    </td>
  ),
}

export function MarkdownMessage({ children, className = 'prose prose-invert prose-sm max-w-none' }) {
  return (
    <ReactMarkdown
      className={className}
      remarkPlugins={[remarkGfm]}
      components={assistantMarkdownComponents}
    >
      {children || ''}
    </ReactMarkdown>
  )
}

// A compact tool card matching the review window / chat tool-log aesthetic.
export function ToolCard({ children }) {
  return (
    <div className="text-xs bg-[#1a1a2e] px-2 py-1 rounded border border-[#3a3a5a] text-[#8888a8] font-mono">
      {children}
    </div>
  )
}

export default MarkdownMessage
