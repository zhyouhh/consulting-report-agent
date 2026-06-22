import { test } from 'node:test'
import assert from 'node:assert/strict'
import { capPayload, validateNewPassword, summarizeUser } from '../src/utils/adminApi.js'

test('capPayload: 空字符串 → null（回退全局/默认）；非空 → 字符串（后端 Decimal 解析）', () => {
  assert.deepEqual(capPayload(''), { daily_cost_yuan: null })
  assert.deepEqual(capPayload('20'), { daily_cost_yuan: '20' })   // 字符串，匹配后端 AdminCapBody: str|None
})

test('capPayload: 非法输入抛错', () => {
  assert.throws(() => capPayload('abc'))
  assert.throws(() => capPayload('-5'))
})

test('validateNewPassword: 长度下限 8', () => {
  assert.equal(validateNewPassword('1234567'), false)
  assert.equal(validateNewPassword('12345678'), true)
})

test('summarizeUser: 额度比例 [0,1]', () => {
  const s = summarizeUser({ today_cost_yuan: 2.5, daily_cap_yuan: 5 })
  assert.equal(s.ratio, 0.5)
})
