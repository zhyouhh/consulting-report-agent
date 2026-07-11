import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const tour = readFileSync(new URL('../src/components/OnboardingTour.jsx', import.meta.url), 'utf8')
const app = readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8')

test('OnboardingTour：完成/跳过回写后端 + 失败不挡使用', () => {
  // 终身一次的真值在服务端：完成与跳过都 POST /api/auth/onboarded
  assert.match(tour, /axios\.post\('\/api\/auth\/onboarded'\)/)
  // 回写失败也要 onDone（finally）——引导绝不能把用户困在弹层里
  assert.match(tour, /finally\s*\{\s*\n?\s*onDone\(\)/)
  // 跳过与最后一步都走同一个 finish（都回写）
  assert.match(tour, /onClick=\{finish\}[\s\S]*?跳过/)
  assert.match(tour, /开始使用/)
})

test('App 门控：严格 === false 才弹 + onDone 不改 init effect 依赖字段', () => {
  // 严格 false：老会话 /me 无 onboarded 字段（undefined）不弹，fail-closed 不打扰
  assert.match(app, /authUser\.onboarded === false && \(/)
  assert.match(app, /<OnboardingTour/)
  // onDone 只翻 onboarded 一个字段（{...prev}），不得动 uid/must_change_password
  //（init effect 依赖 [uid, must_change_password]，动了会整树重挂黑屏——既有雷区）
  assert.match(app, /onDone=\{\(\) => setAuthUser\(prev => \(prev \? \{ \.\.\.prev, onboarded: true \} : prev\)\)\}/)
})

test('OnboardingTour 位于 shell 分支之外（桌面/移动两壳通用）', () => {
  // 渲染点在 isMobile 三元分支闭合之后（`)}` 之后出现），fixed 覆盖层与两壳零布局耦合
  const ternaryClose = app.indexOf(')}\n      {/* 初次使用引导')
  assert.ok(ternaryClose > 0, 'OnboardingTour 渲染点必须紧跟 isMobile 三元分支闭合')
  assert.match(tour, /fixed inset-0 z-50/)
})
