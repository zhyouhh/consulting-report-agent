// 工具事件纯函数（无副作用、无文本解析、无 emoji 字符）。
// reload 的结构化数据由后端给，前端只 reduce live SSE。

/**
 * 取 args 对象第一个值，截断至 40 字符。
 * @param {object} args
 * @returns {string}
 */
export function firstArgValue(args) {
  const d = args && typeof args === 'object' && !Array.isArray(args) ? args : {}
  const keys = Object.keys(d)
  if (!keys.length) return ''
  const val = String(d[keys[0]])
  return val.length > 40 ? val.slice(0, 37) + '...' : val
}

/**
 * 将一个 SSE 事件 reduce 到 ToolEvent 列表。
 * ToolEvent = { id, tool, arg, status: 'pending'|'success'|'error', summary }
 *
 * 同一 id 的 tool_call 可能先发空 arg pending、再发带完整 arg——
 * 后发的 arg 用 ?? 覆盖（'' ?? x === '' 不回退，对的：早发空串就是空串）。
 *
 * @param {Array} list  当前 ToolEvent 数组（不可变）
 * @param {object} event  SSE 事件 { type, id, tool, arg?, status?, summary? }
 * @returns {Array}  新 ToolEvent 数组
 */
export function reduceToolEvent(list = [], event = {}) {
  const id = event.id
  if (!id) return list

  const idx = list.findIndex(e => e.id === id)

  if (event.type === 'tool_call') {
    if (idx === -1) {
      // 新条目
      return [...list, { id, tool: event.tool || '', arg: event.arg || '', status: 'pending', summary: '' }]
    }
    // 已有条目（晚发带完整 arg 的 pending 更新）
    const c = list.slice()
    c[idx] = { ...c[idx], tool: event.tool ?? c[idx].tool, arg: event.arg ?? c[idx].arg }
    return c
  }

  if (event.type === 'tool_result') {
    if (idx === -1) {
      // result 先到，建条目
      return [...list, { id, tool: event.tool || '', arg: '', status: event.status || 'error', summary: event.summary ?? '' }]
    }
    // 更新已有条目：条目已由 tool_call（或更早 result）建立、tool/arg 已正确，
    // 这里只更新 status/summary。
    const c = list.slice()
    c[idx] = { ...c[idx], status: event.status || 'error', summary: event.summary ?? '' }
    return c
  }

  return list
}
