import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// 2026-07-06：管理控制台从弹窗（AdminPanel）升级为 /admin 独立页面（AdminPage）。
const src = readFileSync(new URL('../src/components/AdminPage.jsx', import.meta.url), 'utf8')
const mainSrc = readFileSync(new URL('../src/main.jsx', import.meta.url), 'utf8')

test('main.jsx 按 pathname 分流 /admin → AdminPage（含 StrictMode/ErrorBoundary 包裹）', () => {
  assert.match(mainSrc, /\/\^\\\/admin\\\/\?\$\//)   // /^\/admin\/?$/ 正则路由
  assert.match(mainSrc, /isAdminRoute \? <AdminPage \/> : <App \/>/)
  assert.match(mainSrc, /<ErrorBoundary>/)
})

test('AdminPage 覆盖全部 admin 端点 + 历史用量端点 + 复用 adminApi', () => {
  assert.match(src, /\/api\/admin\/users/)
  assert.match(src, /\/api\/admin\/users\/\$\{[^}]+\}\/(password|cap|disabled)/)
  assert.match(src, /\/api\/admin\/invite-code\/rotate/)
  assert.match(src, /\/api\/admin\/allowed-hosts/)
  assert.match(src, /\/api\/admin\/usage\?days=30/)
  assert.match(src, /from '\.\.\/utils\/adminApi'/)
  assert.match(src, /from '\.\.\/utils\/adminUsage'/)
})

test('AdminPage 鉴权自理：me 判定 + 三种拦截态给出返回主页出口', () => {
  // 背景鉴权检查不触发全局 401 登出副作用
  assert.match(src, /axios\.get\('\/api\/auth\/me', \{ skipUnauthedHandler: true \}\)/)
  for (const state of ['unauth', 'forbidden', 'mustchange']) {
    assert.match(src, new RegExp(state), `应处理 ${state} 拦截态`)
  }
  assert.match(src, /返回主页/)
})

test('AdminPage 错误 detail 一律经 normalizeAuthError 归一（422 数组直渲染会白屏）', () => {
  assert.match(src, /from '\.\.\/utils\/authError'/)
  assert.match(src, /normalizeAuthError\(/)
  // err 渲染为字符串插值，且所有 axios catch 不把 detail 原样 setErr
  assert.doesNotMatch(src, /setErr\(e\?\.response\?\.data\?\.detail/)
})

test('AdminPage 主题随 token 体系：无裸 hex / 无 dark: 前缀（scrim 例外也不该出现在整页）', () => {
  assert.doesNotMatch(src, /#[0-9a-fA-F]{3,8}\b/)
  assert.doesNotMatch(src, /className="[^"]*dark:/)
})

test('AdminPage 图表纯 CSS/DIV 实现（不引图表库）+ hover 明细 title', () => {
  assert.doesNotMatch(src, /from 'recharts'|from 'chart\.js'|from 'echarts'/)
  assert.match(src, /缓存命中率/)
  assert.match(src, /role="img"/)
})
