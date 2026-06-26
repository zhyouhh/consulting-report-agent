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

// 侧边栏额度数字曾陈旧（只随初始 /me 拉一次）：聊天每轮结束 + 窗口聚焦时须刷新 /me 额度字段。
test('App: 额度刷新 wiring（轮结束 + 聚焦都刷新 /me）', () => {
  // refreshAuthQuota 重新 GET /me 并只并入 cost 字段（保 prev 在、不复活已登出用户）。
  const refreshBlock = src.match(/const refreshAuthQuota[\s\S]*?\n  \}/)
  assert.ok(refreshBlock, '应定义 refreshAuthQuota')
  assert.match(refreshBlock[0], /axios\.get\(['"]\/api\/auth\/me['"]/, 'refreshAuthQuota 应重拉 /me')
  assert.match(refreshBlock[0], /today_cost_yuan[\s\S]*?daily_cap_yuan/, '应并入 today_cost_yuan/daily_cap_yuan')
  // codex BLOCKER：背景轮询跳过全局 401 登出副作用，防旧用户在途 /me 误踢新用户到登录页。
  assert.match(refreshBlock[0], /skipUnauthedHandler:\s*true/, 'refreshAuthQuota 应带 skipUnauthedHandler')
  // codex BLOCKER①：序号守卫——只让最后发起的 /me 回包落地，防同 uid 乱序回包覆盖回旧额度。
  assert.match(refreshBlock[0], /\+\+quotaRefreshSeqRef\.current/, 'refreshAuthQuota 应自增请求序号')
  assert.match(refreshBlock[0], /seq\s*!==\s*quotaRefreshSeqRef\.current[\s\S]{0,40}?return prev/, '非最新序号的回包应丢弃')
  // codex BLOCKER②：合并前校验回包 uid 与当前 prev.uid 一致，防在途 /me 跨用户串号。
  assert.match(refreshBlock[0], /r\.data\??\.uid\s*!==\s*prev\.uid[\s\S]{0,40}?return prev/, 'uid 不符则原样返回 prev')
  // 统一回调 handleProjectMutated（刷 workspace + 刷额度），ChatPanel 与 WorkspacePanel 共用——
  // 任一计费路径（聊天轮 / 独立审查后系统轮 / 文件保存）完成都即时刷额度。
  assert.match(
    src,
    /const handleProjectMutated\s*=\s*\(\)\s*=>\s*\{[\s\S]{0,160}?refreshAuthQuota\(\)/,
    'handleProjectMutated 应同时刷 workspace 与额度',
  )
  const panelMutatedWirings = src.match(/onProjectMutated=\{handleProjectMutated\}/g) || []
  assert.ok(panelMutatedWirings.length >= 2, 'ChatPanel 与 WorkspacePanel 都应 onProjectMutated={handleProjectMutated}')
  // 窗口重新聚焦也刷新（兜独立审查等非聊天计费路径 + 跨标签页）。
  assert.match(
    src,
    /onFocus[\s\S]{0,120}?refreshAuthQuota\(\)[\s\S]{0,120}?addEventListener\(['"]focus['"]/,
    'focus effect 应在聚焦时刷新额度',
  )
})

// 回归守卫：initializeApp effect 必须只依赖稳定身份字段（uid + must_change_password），
// 不可依赖整个 authUser 对象——否则 refreshAuthQuota 每轮造新 authUser 引用会重跑
// initializeApp → setLoading(true) → 整树卸载重挂（黑屏闪 + 聊天/工具调用记录全丢）。
test('App: initializeApp effect 依赖稳定身份字段而非整个 authUser（防额度刷新触发重初始化黑屏）', () => {
  assert.match(
    src,
    /initializeApp\(\)\s*\n\s*\}\s*\n\s*\},\s*\[authUser\?\.uid,\s*authUser\?\.must_change_password\]\)/,
    'initializeApp effect 依赖应为 [authUser?.uid, authUser?.must_change_password]',
  )
  assert.doesNotMatch(
    src,
    /initializeApp\(\)\s*\n\s*\}\s*\n\s*\},\s*\[authUser\]\)/,
    'initializeApp effect 不应依赖整个 authUser 对象（额度刷新会触发重初始化黑屏）',
  )
})

// 侧栏副标题阶段名 race 修复（codex NIT）：传给 Sidebar 的实时 stage 必须按
// 「workspace 归属项目 === 当前活动项目」守卫，否则切项目时旧项目 stage 会瞬时覆盖新项目。
test('App: 侧栏 currentStageCode 受 workspaceProjectId 守卫（防切项目时旧 stage 覆盖新项目）', () => {
  // setWorkspaceProjectId 须在 shouldApplyProjectResponse 通过 + setWorkspace 之后才置当前请求项目，
  // 不能在 fetch 一开始就乐观置位（否则守卫失去意义）。
  assert.match(
    src,
    /setWorkspace\(res\.data\)\s*\n\s*setWorkspaceProjectId\(requestProject\)/,
    'workspace 成功加载后才绑定归属项目 id（紧跟 setWorkspace(res.data)）',
  )
  // 传给 Sidebar 的实时 stage 必须门控在归属匹配，否则回退 undefined（让 Sidebar 用 list 的 stage_code）。
  assert.match(
    src,
    /currentStageCode=\{workspaceProjectId === currentProjectId \? workspace\?\.stage_code : undefined\}/,
    'currentStageCode 应按 workspaceProjectId === currentProjectId 守卫',
  )
})
