import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'
import { extractSseDataPayload } from '../src/utils/chatPresentation.js'

// 聊天消费者（ChatPanel 经此函数）忽略非 data: 行
test('extractSseDataPayload 忽略 : keepalive 心跳注释行（返回 null）', () => {
  assert.strictEqual(extractSseDataPayload(': keepalive'), null)
  assert.strictEqual(extractSseDataPayload(':keepalive'), null)
})

// 审查消费者（IndependentReviewDrawer）跳过非 data: 块——source guard 锁住容忍逻辑
test('IndependentReviewDrawer 跳过非 data: 块（容忍心跳注释）', () => {
  const drawer = readFileSync(new URL('../src/components/IndependentReviewDrawer.jsx', import.meta.url), 'utf8')
  assert.match(drawer, /!block\.startsWith\(\s*['"]data: ['"]\s*\)/, 'drawer 必须跳过非 data: 块')
})
