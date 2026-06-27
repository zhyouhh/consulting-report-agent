import React from 'react'
import ToolCallPill from './ToolCallPill'

// 成组渲染一条 assistant 消息的工具调用 pill（正文上方）。
// toolEvents 形状 = [{ id, tool, arg, status, summary }]（live reduceToolEvent / reload tool_events）。
export default function ToolCallList({ toolEvents }) {
  if (!toolEvents || !toolEvents.length) return null
  return (
    <div className="flex flex-col items-start gap-[6px] mb-[10px]">
      {toolEvents.map(e => <ToolCallPill key={e.id} event={e} />)}
    </div>
  )
}
