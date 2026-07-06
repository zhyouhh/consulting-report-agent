import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// 2026-07-07：搜索池额度监控板块（SearchPoolQuota）——tavily 实时 / brave 被动观测 /
// serper·exa 本地估算，三种数据质量在卡片上明示；趋势图复用 usageChart 纯函数。
const src = readFileSync(new URL('../src/components/SearchPoolQuota.jsx', import.meta.url), 'utf8')
const adminSrc = readFileSync(new URL('../src/components/AdminPage.jsx', import.meta.url), 'utf8')

test('AdminPage：搜索池额度独立取数（不进 reload 的 Promise.all）+ 强刷参数 + 组件接线', () => {
  assert.match(adminSrc, /import SearchPoolQuota from '\.\/SearchPoolQuota'/)
  assert.match(adminSrc, /\/api\/admin\/search-quota\$\{refresh \? '\?refresh=true' : ''\}/)
  assert.match(adminSrc, /<SearchPoolQuota data=\{searchQuota\} error=\{searchQuotaErr\} \/>/)
  // 独立 effect 取数：tavily 实时查询慢/失败不拖累核心管理数据
  assert.match(adminSrc, /if \(authState === 'ready'\) loadSearchQuota\(\)/)
  const promiseAllBlock = adminSrc.match(/Promise\.all\(\[[\s\S]*?\]\)/)[0]
  assert.doesNotMatch(promiseAllBlock, /search-quota/)
  // 错误经 normalizeAuthError 归一（422 数组直渲染会白屏）
  assert.match(adminSrc, /setSearchQuotaErr\(normalizeAuthError\(/)
})

test('SearchPoolQuota：复用 usageChart/adminUsage 纯函数 + searchQuota 文案层', () => {
  assert.match(src, /from '\.\.\/utils\/searchQuota'/)
  assert.match(src, /from '\.\.\/utils\/adminUsage'/)
  assert.match(src, /from '\.\.\/utils\/usageChart'/)
  assert.match(src, /smoothPathD/)
  assert.match(src, /listDays\(data\.since, data\.today\)/)
})

test('序列颜色类三份写死（Tailwind JIT 字面量扫描），禁运行时拼类名', () => {
  for (const cls of ['stroke-accent', 'stroke-success', 'stroke-warn', 'stroke-abright']) {
    assert.match(src, new RegExp(`'${cls}'`), `缺少字面量 ${cls}`)
  }
  assert.doesNotMatch(src, /replace\('bg-'/)
  assert.doesNotMatch(src, /replace\('stroke-'/)
  assert.doesNotMatch(src, /`(stroke|fill|bg)-\$\{/)   // 模板串拼 token 类名
})

test('数据质量诚实展示：估算标注 +（估算）后缀 + 口径提示', () => {
  assert.match(src, /sourceMeta/)
  assert.match(src, /provider\.source === 'estimated' \? '（估算）' : ''/)
  assert.match(src, /meta\.hint/)
})

test('降级路径：无数据加载中 / 未配置搜索池 / 错误行', () => {
  assert.match(src, /正在读取搜索池额度/)
  assert.match(src, /未配置搜索池/)
  assert.match(src, /暂无调用记录/)
})

test('无裸 hex、无 dark: 前缀（主题 token 铁律）', () => {
  assert.doesNotMatch(src, /#[0-9a-fA-F]{3,8}\b/)
  assert.doesNotMatch(src, /dark:/)
})
