import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Shared markdown rendering for assistant prose, reused by the main ChatPanel and the S5
// ReviewChatWindow so both render identically (GFM tables, dark-theme cells). The content is
// expected to be ALREADY backend-stripped of <think> blocks — do NOT re-strip in the frontend.
export const assistantMarkdownComponents = {
  p: ({ children }) => (
    <p className="my-1.5 leading-[1.68]">{children}</p>
  ),
  a: ({ children, ...props }) => (
    <a className="text-abright underline underline-offset-2" {...props}>{children}</a>
  ),
  code: ({ node, inline, children, ...props }) => {
    const isInline = inline ?? (node?.position?.start?.line === node?.position?.end?.line)
    return isInline
      ? <code className="rounded bg-asoft px-1.5 py-0.5 font-mono text-13 text-asoftt" {...props}>{children}</code>
      : <code className="font-mono text-13" {...props}>{children}</code>
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-ibtn border border-border bg-card2 p-3 font-mono text-13">{children}</pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-col pl-3 text-t2">{children}</blockquote>
  ),
  h1: ({ children }) => (
    <h1 className="font-semibold text-text text-lg mt-3 mb-1.5">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="font-semibold text-text text-base mt-3 mb-1.5">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="font-semibold text-text text-15 mt-3 mb-1.5">{children}</h3>
  ),
  ul: ({ children }) => (
    <ul className="my-1.5 list-disc pl-5">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-1.5 list-decimal pl-5">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="my-0.5">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-text">{children}</strong>
  ),
  hr: () => (
    <hr className="my-3 border-col" />
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-13">
        {children}
      </table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-card2 px-2 py-1 text-left font-semibold text-text">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-border px-2 py-1 align-top text-text">
      {children}
    </td>
  ),
}

export function MarkdownMessage({ children, className = 'max-w-none text-15 leading-[1.68] text-text' }) {
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
    <div className="text-xs bg-card2 px-2 py-1 rounded border border-border text-t2 font-mono">
      {children}
    </div>
  )
}

export default MarkdownMessage
