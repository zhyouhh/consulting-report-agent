import React, { useState } from 'react'
import { IconTool, IconCheck, IconClose, IconChevronDown } from './icons'

export default function ToolCallPill({ event }) {
  const [open, setOpen] = useState(false)
  if (!event) return null
  const { tool = '', arg = '', status = 'pending', summary = '' } = event
  const expandable = Boolean(summary)
  const statusText = status === 'success' ? '成功' : status === 'error' ? '失败' : '进行中'
  const Tag = expandable ? 'button' : 'div'
  const tagProps = expandable
    ? { type: 'button', onClick: () => setOpen(o => !o), 'aria-expanded': open }
    : {}
  return (
    <div className="inline-flex flex-col max-w-full min-w-0">
      <Tag
        {...tagProps}
        className={`inline-flex items-center gap-[9px] border border-border rounded-ibtn bg-card2 px-[11px] py-[7px] font-mono min-w-0 text-left${expandable ? ' cursor-pointer' : ''}`}
      >
        <IconTool size={13} className="text-abright flex-shrink-0" aria-hidden="true" />
        <span className="text-xs text-text whitespace-nowrap flex-shrink-0">{event.tool}</span>
        {event.arg && (
          <span className="text-11 text-t3 truncate min-w-0">{event.arg}</span>
        )}
        <span className="sr-only">{statusText}</span>
        {status === 'success' && (
          <IconCheck size={13} className="text-success flex-shrink-0 ml-auto" aria-hidden="true" />
        )}
        {status === 'error' && (
          <IconClose size={13} className="text-error flex-shrink-0 ml-auto" aria-hidden="true" />
        )}
        {status === 'pending' && (
          <span
            className="ml-auto w-[7px] h-[7px] rounded-full bg-t3 animate-pulse flex-shrink-0"
            aria-hidden="true"
          />
        )}
        {expandable && (
          <IconChevronDown
            size={12}
            className={`text-t3 flex-shrink-0 transition-transform${open ? ' rotate-180' : ''}`}
            aria-hidden="true"
          />
        )}
      </Tag>
      {expandable && open && (
        <div className="text-11 text-t3 font-mono px-[11px] py-[5px] break-words">
          {summary}
        </div>
      )}
    </div>
  )
}
