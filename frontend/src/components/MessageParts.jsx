import React from 'react'
import ToolCallPill from './ToolCallPill'
import { renderAssistantText } from './assistantTextRender'

// 按时间线顺序渲染一条 assistant 消息的有序片段（text / tool 交错穿插）。
// parts 形状 = [{ type:'text', text } | { type:'tool', id, tool, arg, status, summary }]（messageParts.js 算子）。
export default function MessageParts({ parts }) {
  if (!parts || !parts.length) return null
  return (
    <div className="flex flex-col items-stretch gap-[6px]">
      {parts.map((p, i) => p.type === 'tool'
        ? <div key={p.id || `t${i}`} className="flex"><ToolCallPill event={p} /></div>
        : <div key={`x${i}`}>{renderAssistantText(p.text)}</div>)}
    </div>
  )
}
