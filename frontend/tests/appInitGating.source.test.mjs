import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')

// NIT 1：must_change_password 用户登录后不应触发 initializeApp（否则项目/设置请求被后端 403
// → 闪「加载项目列表失败」错误弹窗，再渲染强制改密屏）。改成 effect 驱动、按 must_change_password gate。
test('App: initializeApp 调用受 must_change_password 守卫（不在改密前加载主界面数据）', () => {
  // 初始化用 effect 驱动 + 守卫：effect 依赖 authUser，体内同时引用 must_change_password 与 initializeApp。
  assert.match(
    src,
    /must_change_password[\s\S]{0,160}?initializeApp\s*\(/,
    'initializeApp 调用应在 must_change_password 守卫之内（gate 在改密完成后）',
  )
  // 不再有「无条件 .then(...initializeApp())」搭在初始 /api/auth/me 上。
  assert.doesNotMatch(
    src,
    /setAuthUser\(r\.data\);\s*return initializeApp\(\)/,
    '初始 /api/auth/me 不应无条件链 initializeApp（须先过 must_change_password 守卫）',
  )
})

// NIT 2：AdminPanel 挂载兜底再 gate 一层 is_admin（入口已 gated，showAdmin 只可能被 admin 置真，但纵深防御）。
test('App: AdminPanel 挂载额外兜底 is_admin', () => {
  assert.match(
    src,
    /showAdmin\s*&&\s*authUser\??\.is_admin\s*&&\s*<AdminPanel/,
    'AdminPanel 挂载应为 showAdmin && authUser?.is_admin && <AdminPanel ...>',
  )
})
