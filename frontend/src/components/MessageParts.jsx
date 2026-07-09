import React from 'react'
import ToolCallPill from './ToolCallPill'
import { renderAssistantText } from './assistantTextRender'

// 按时间线顺序渲染一条 assistant 消息的有序片段（text / tool 交错穿插）。
// parts 形状 = [{ type:'text', text } | { type:'tool', id, tool, arg, status, summary }]（messageParts.js 算子）。
// onOpenFile（可选）：文件内链回调，透传给 pill 与正文反引号文件名渲染。
export default function MessageParts({ parts, onOpenFile }) {
  if (!parts || !parts.length) return null
  return (
    <div className="flex flex-col items-stretch gap-[6px]">
      {/* key 用索引前缀 + 保留 id：parts 只增不重排，索引天然唯一（防病态 reload 出现重复 tool id
          导致重复 React key），id 后缀保 in-place 更新时 reconciliation 稳定。 */}
      {parts.map((p, i) => p.type === 'tool'
        ? <div key={`${i}-${p.id || 'tool'}`} className="flex"><ToolCallPill event={p} onOpenFile={onOpenFile} /></div>
        : <div key={`x${i}`}>{renderAssistantText(p.text, { onOpenFile })}</div>)}
    </div>
  )
}
