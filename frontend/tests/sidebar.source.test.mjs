import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
test('Sidebar account block', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(s, /\/api\/auth\/logout/) // Sidebar 只发意图，App 先停全部工作再请求后端
  assert.match(s, /authUser\.username/)          // 显示用户名
  assert.match(s, /登出/)
  assert.match(s, /onLoggedOut\?\.\(\)/)         // 登出后回调清本地态（即便 POST 失败也要 fire）
  // 桌面/本地（uid==="local"）不显示账号块，避免点登出困在登录页
  assert.match(s, /authUser\.uid !== ['"]local['"]/)
})

test('Sidebar 显示后台生成项目的 pulse 圆点', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  assert.match(s, /busyProjectIds\.has\(project\.id\)/)
  assert.match(s, /w-1\.5 h-1\.5 rounded-full bg-abright[^"']*animate-pulse/)
})

test('Sidebar 项目副标题 = 报告类型 · 阶段名（getStageName + currentStageCode）', () => {
  const s = readFileSync(new URL('../src/components/Sidebar.jsx', import.meta.url), 'utf8')
  // 阶段名走单一真值源 STAGE_NAMES（getStageName），不在 Sidebar 重复硬编中文
  assert.match(s, /import\s*\{[^}]*getStageName[^}]*\}\s*from\s*['"][^'"]*workspaceSummary/)
  assert.match(s, /getStageName\(/)
  // 活动项目取实时 workspace stage，其余取 list_projects 的 stage_code
  assert.match(s, /currentStageCode/)
  assert.match(s, /project\.stage_code/)
  // 「报告类型 · 阶段名」拼接
  assert.match(s, /' · '/)
})

test('ProjectCreateModal 标题/副标题/各字段 label 齐全', () => {
  const s = readFileSync(new URL('../src/components/ProjectCreateModal.jsx', import.meta.url), 'utf8')
  assert.match(s, /新建报告/)
  assert.match(s, /填写基本信息，助手会据此开始准备阶段。/)
  assert.match(s, /报告类型/)
  assert.match(s, /报告主题/)
  assert.match(s, /截止日期/)
  assert.match(s, /预期篇幅/)
})

test('AdminPage 用户表保留可编辑额度 + 状态色标 + grid 表头', () => {
  // 2026-07-06：AdminPanel 弹窗升级为 /admin 独立页面（AdminPage），本约束原样保留。
  const s = readFileSync(new URL('../src/components/AdminPage.jsx', import.meta.url), 'utf8')
  // 额度列仍是可编辑 input（用户明确要求保留）
  assert.match(s, /onBlur=\{\(e\) => setCap\(/)
  // 状态用 success/warn token 着色（正常绿 / 已禁用橙）
  assert.match(s, /text-warn/)
  assert.match(s, /text-success/)
  // grid 表头（替换原裸 table）
  assert.match(s, /grid-cols-\[1\.6fr_1fr_1fr_1fr_1\.3fr\]/)
})
