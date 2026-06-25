import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeAuthError, normalizeApiErrorDetail } from '../src/utils/authError.js'

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

// normalizeApiErrorDetail：通用版，入参是**已取出的** detail 值（非 axios error）。
// 用于 IndependentReviewDrawer 等 fetch 流手解析 `{detail: ...}` 的场景——必须永远返回字符串，
// 杜绝把 422 数组 / 对象塞进 React 子节点导致整窗崩溃（codex spec BLOCKER）。

test('normalizeApiErrorDetail: string 原样返回、空白回退', () => {
  assert.equal(normalizeApiErrorDetail('启动审查失败：上游超时', '兜底'), '启动审查失败：上游超时')
  assert.equal(normalizeApiErrorDetail('   ', '兜底'), '兜底')
})

test('normalizeApiErrorDetail: 422 数组取首条 msg、恒为字符串、不暴露数组', () => {
  const out = normalizeApiErrorDetail(
    [{ loc: ['body', 'run_id'], msg: 'field required', type: 'value_error.missing' }],
    '启动审查失败',
  )
  assert.equal(typeof out, 'string')
  assert.ok(!out.includes('[') && !out.includes('{'), '不得暴露原始数组/对象')
  assert.equal(out, 'field required')
})

test('normalizeApiErrorDetail: 对象取 msg/message，否则回退；恒字符串', () => {
  assert.equal(normalizeApiErrorDetail({ msg: '内部错误' }, '兜底'), '内部错误')
  assert.equal(normalizeApiErrorDetail({ message: '下游 500' }, '兜底'), '下游 500')
  assert.equal(typeof normalizeApiErrorDetail({ code: 500 }, '兜底'), 'string')
  assert.equal(normalizeApiErrorDetail({ code: 500 }, '兜底'), '兜底')
})

test('normalizeApiErrorDetail: 空 / 数字 / 数组无 msg → fallback，结果恒字符串', () => {
  for (const bad of [undefined, null, 42, true, [{ loc: ['x'] }]]) {
    const out = normalizeApiErrorDetail(bad, '启动审查失败')
    assert.equal(typeof out, 'string', `detail=${JSON.stringify(bad)} 应归一成字符串`)
  }
  assert.equal(normalizeApiErrorDetail(undefined, '启动审查失败'), '启动审查失败')
})

test('normalizeApiErrorDetail: 误传非字符串 fallback 仍返回字符串', () => {
  assert.equal(typeof normalizeApiErrorDetail(undefined, { bad: 1 }), 'string')
  assert.equal(typeof normalizeApiErrorDetail({ code: 1 }, null), 'string')
})
