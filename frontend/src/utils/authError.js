// 把后端错误响应的 detail 归一成**字符串**再交给 UI 显示。
//
// FastAPI 的 detail 形态有两种：
//   - HTTPException → 字符串（如 401「用户名或密码错误」、409「用户名已被占用」）
//   - 422 校验错误 → 数组 [{loc, msg, type}, ...]（如密码<6 位 / 用户名<3 位）
// 旧 Login 直接 setErr(detail)，遇 422 时 err 变成对象数组 → 渲染 {err} 触发
// React "Objects are not valid as a React child" → 登录页（不在 ErrorBoundary 内）整树卸载 →
// 白屏、控制台外无任何提示。本函数确保**永远返回字符串**，杜绝该崩溃。
export function normalizeAuthError(error, fallback = '操作失败，请重试') {
  // fallback 内部归一：作为导出工具，即便调用方误传非字符串也保证返回字符串（codex NIT）。
  const safeFallback = (typeof fallback === 'string' && fallback.trim()) ? fallback : '操作失败，请重试'
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') {
    return detail.trim() ? detail : safeFallback
  }
  if (Array.isArray(detail)) {
    // 422 校验错误数组（长度/类型/最大值等）：给通用提示——短长度已被客户端预校验拦在提交前，
    // 漏到这里的多是别的校验（如超长），故不写死「至少 N 位」以免误导（codex NIT）。
    return '用户名或密码格式不符合要求'
  }
  return safeFallback
}

// 通用版：把**任意已取出的** detail 值（string | 数组 | 对象 | 空）归一成字符串。
// 用于任何把后端 detail 直接显示给用户的地方（非 axios-shaped error，如 fetch 流手解析出的
// `{detail: ...}`）。FastAPI 422 的 detail 是 [{loc,msg,type}] 数组、HTTPException 是字符串；
// 绝不能把数组/对象塞进 React 子节点（会触发 "Objects are not valid as a React child" 崩溃）。
// 与 normalizeAuthError 刻意分开：后者对数组返回固定的登录文案，此处取首条 msg 更通用。
export function normalizeApiErrorDetail(detail, fallback = '操作失败，请重试') {
  const safeFallback = (typeof fallback === 'string' && fallback.trim()) ? fallback : '操作失败，请重试'
  if (typeof detail === 'string') {
    return detail.trim() ? detail : safeFallback
  }
  if (Array.isArray(detail)) {
    const firstMsg = detail.find(item => item && typeof item.msg === 'string')?.msg
    return firstMsg && firstMsg.trim() ? firstMsg : safeFallback
  }
  if (detail && typeof detail === 'object') {
    const msg = typeof detail.msg === 'string'
      ? detail.msg
      : typeof detail.message === 'string'
        ? detail.message
        : null
    return msg && msg.trim() ? msg : safeFallback
  }
  return safeFallback
}
