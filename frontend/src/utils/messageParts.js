// per-event 片段算子（无副作用、无 emoji）。各事件 handler 调对应算子建 parts，content 装配本身不改。

export function mutateCurrentTextPart(parts = [], fn) {
  const idx = parts.length - 1
  if (idx >= 0 && parts[idx].type === 'text') {
    const c = parts.slice(); c[idx] = { ...c[idx], text: fn(c[idx].text) }; return c
  }
  return [...parts, { type: 'text', text: fn('') }]
}

export function applyToolEventToParts(parts = [], event = {}) {
  const id = event.id
  if (!id) return parts
  const idx = parts.findIndex(p => p.type === 'tool' && p.id === id)
  if (event.type === 'tool_call') {
    if (idx === -1) return [...parts, { type: 'tool', id, tool: event.tool || '', arg: event.arg || '', status: 'pending', summary: '' }]
    const c = parts.slice(); c[idx] = { ...c[idx], tool: event.tool || c[idx].tool, arg: event.arg ?? c[idx].arg }; return c
  }
  if (event.type === 'tool_result') {
    if (idx === -1) return [...parts, { type: 'tool', id, tool: event.tool || '', arg: '', status: event.status || 'error', summary: event.summary ?? '' }]
    const c = parts.slice(); c[idx] = { ...c[idx], status: event.status || 'error', summary: event.summary ?? '' }; return c
  }
  return parts
}

export function closePendingToolParts(parts, summary = '') {
  if (!parts || !parts.length) return parts
  return parts.map(p => p.type === 'tool' && p.status === 'pending' ? { ...p, status: 'error', summary: p.summary || summary } : p)
}

export function appendErrorPart(parts = [], text = '') {
  if (!text) return parts
  return [...parts, { type: 'text', text }]
}

export function partsToText(parts) {
  if (!parts || !parts.length) return ''
  return parts.filter(p => p.type === 'text').map(p => p.text).join('')
}
