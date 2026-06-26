import React, { useState } from 'react'
import { IconTool, IconCheck, IconClose, IconChevronDown } from './icons'

export default function ToolCallPill({ event }) {
  const [open, setOpen] = useState(false)
  if (!event) return null
  const { tool = '', arg = '', status = 'pending', summary = '' } = event
  const expandable = Boolean(summary)
  return (
    <div className="inline-flex flex-col max-w-full min-w-0">
      <div
        className={`inline-flex items-center gap-[9px] border border-border rounded-ibtn bg-card2 px-[11px] py-[7px] font-mono min-w-0${expandable ? ' cursor-pointer' : ''}`}
        onClick={expandable ? () => setOpen(o => !o) : undefined}
        role={expandable ? 'button' : undefined}
        aria-expanded={expandable ? open : undefined}
      >
        <IconTool size={13} className="text-abright flex-shrink-0" />
        <span className="text-xs text-text whitespace-nowrap flex-shrink-0">{event.tool}</span>
        {event.arg && (
          <span className="text-11 text-t3 truncate min-w-0">{event.arg}</span>
        )}
        {status === 'success' && (
          <IconCheck size={13} className="text-success flex-shrink-0 ml-auto" />
        )}
        {status === 'error' && (
          <IconClose size={13} className="text-error flex-shrink-0 ml-auto" />
        )}
        {status === 'pending' && (
          <span
            className="ml-auto w-[7px] h-[7px] rounded-full bg-t3 animate-pulse flex-shrink-0"
            aria-label="进行中"
          />
        )}
        {expandable && (
          <IconChevronDown
            size={12}
            className={`text-t3 flex-shrink-0 transition-transform${open ? ' rotate-180' : ''}`}
          />
        )}
      </div>
      {expandable && open && (
        <div className="text-11 text-t3 font-mono px-[11px] py-[5px] break-words">
          {summary}
        </div>
      )}
    </div>
  )
}
