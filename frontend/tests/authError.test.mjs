import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeAuthError } from '../src/utils/authError.js'

// 根因回归（2026-06-23 调试）：FastAPI 422 校验错误的 detail 是 [{loc,msg,type}] 数组，
// 旧 Login 直接 setErr(detail) → 渲染 {err} 时 React 抛 "Objects are not valid as a React
// child" → 登录页（无 ErrorBoundary）整树卸载 → 白屏。normalizeAuthError 必须**永远返回字符串**。

test('string detail（401/403/409）原样返回', () => {
  assert.equal(
    normalizeAuthError({ response: { data: { detail: '用户名或密码错误' } } }),
    '用户名或密码错误',
  )
})

test('422 数组 detail → 通用字符串提示（绝不是对象/数组）', () => {
  const err = { response: { data: { detail: [
    { loc: ['body', 'password'], msg: 'String should have at least 6 characters', type: 'string_too_short' },
  ] } } }
  const out = normalizeAuthError(err)
  assert.equal(typeof out, 'string')
  assert.ok(out.length > 0 && !out.includes('['), '应是友好字符串、不暴露原始数组')
  assert.match(out, /格式不符合/)
})

test('非字符串 fallback 被误传时仍返回字符串（导出工具硬化）', () => {
  assert.equal(typeof normalizeAuthError({}, { bad: 1 }), 'string')
  assert.equal(typeof normalizeAuthError(undefined, null), 'string')
})

test('缺失 detail → fallback 字符串', () => {
  assert.equal(normalizeAuthError({}), '操作失败，请重试')
  assert.equal(normalizeAuthError(undefined), '操作失败，请重试')
  assert.equal(normalizeAuthError({ response: { data: {} } }), '操作失败，请重试')
})

test('非字符串非数组的 detail（对象/数字）→ fallback，结果恒为字符串', () => {
  for (const bad of [{ x: 1 }, 42, true, null]) {
    const out = normalizeAuthError({ response: { data: { detail: bad } } })
    assert.equal(typeof out, 'string', `detail=${JSON.stringify(bad)} 应归一成字符串`)
  }
})

test('空白字符串 detail → fallback（不显示空错误）', () => {
  assert.equal(normalizeAuthError({ response: { data: { detail: '   ' } } }), '操作失败，请重试')
})
