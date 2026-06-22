import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('ChatPanel stream fetch 带 credentials', () => {
  const s = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')
  assert.match(s, /credentials:\s*'include'/)
})

test('IndependentReviewDrawer 两个 fetch（stream + discard）都带 credentials', () => {
  const s = readFileSync(new URL('../src/components/IndependentReviewDrawer.jsx', import.meta.url), 'utf8')
  const hits = (s.match(/credentials:\s*'include'/g) || []).length
  assert.ok(hits >= 2, `应有 ≥2 处 credentials（stream + discard），实际 ${hits}`)
})

// NIT 3（codex 强化）：纯字符串计数不能证明每个 fetch 自己带 credentials（可能 2 个 fetch
// 共享 1 个 credentials、另一个漏）。无 jsdom 下尽量绑定：用就近正则把「fetch( 选项对象起始
// → credentials」绑在一个调用范围内（credentials 紧跟 method 行、<120 字内），再断言这种
// 绑定命中数 == 实际 fetch( 调用数，保证每个 fetch 都各自带 credentials。固有限制：source-guard
// 无法执行代码，proximity 是逼近上限。
function assertEveryFetchBindsCredentials(src, label) {
  const fetchCalls = (src.match(/\bfetch\(/g) || []).length
  const bound = (src.match(/\bfetch\([\s\S]{0,160}?credentials:\s*'include'/g) || []).length
  assert.equal(bound, fetchCalls,
    `${label}：每个 fetch( 都应在就近选项对象内带 credentials（fetch 调用 ${fetchCalls} 个，绑定到 credentials 的 ${bound} 个）`)
}

test('ChatPanel 每个 fetch 调用各自带 credentials（proximity 绑定）', () => {
  const s = readFileSync(new URL('../src/components/ChatPanel.jsx', import.meta.url), 'utf8')
  assertEveryFetchBindsCredentials(s, 'ChatPanel')
})

test('IndependentReviewDrawer 每个 fetch 调用各自带 credentials（proximity 绑定）', () => {
  const s = readFileSync(new URL('../src/components/IndependentReviewDrawer.jsx', import.meta.url), 'utf8')
  assertEveryFetchBindsCredentials(s, 'IndependentReviewDrawer')
})
