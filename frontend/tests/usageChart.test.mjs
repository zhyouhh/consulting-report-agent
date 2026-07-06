import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  monotoneTangents, smoothPathD, smoothAreaD, niceCeil, axisMax,
  yPixel, dayXs, nearestIndex, buildTrendData, xLabelIndices,
} from '../src/utils/usageChart.js'

// ── monotoneTangents / smoothPathD ───────────────────────────────────────────

test('monotoneTangents 平坦段切线为零、单点/空输入安全', () => {
  assert.deepEqual(monotoneTangents([5, 5, 5]), [0, 0, 0])
  assert.deepEqual(monotoneTangents([1]), [0])
  assert.deepEqual(monotoneTangents([]), [])
})

test('smoothPathD 空/单点退化，正常序列产出 M+C 且无 NaN', () => {
  assert.equal(smoothPathD([], []), '')
  assert.equal(smoothPathD([10], [20]), 'M 10 20')
  const d = smoothPathD([0, 50, 100], [100, 40, 60])
  assert.match(d, /^M /)
  assert.match(d, / C /)
  assert.doesNotMatch(d, /NaN/)
})

test('smoothPathD 不过冲：尖峰序列的所有控制点 y 不越过数据范围（零值日不画假谷）', () => {
  const ys = [200, 200, 40, 200, 200]   // 像素坐标：y 越小值越大 → 中间尖峰
  const d = smoothPathD([0, 25, 50, 75, 100], ys)
  const nums = d.match(/-?[\d.]+/g).map(Number)
  const yVals = nums.filter((_, i) => i % 2 === 1)   // 奇数位是 y
  for (const y of yVals) {
    assert.ok(y >= 40 - 0.01 && y <= 200 + 0.01, `控制点 y=${y} 过冲出数据范围`)
  }
})

test('smoothAreaD 闭合到底边（L 底边 ×2 + Z）；点数不足返回空', () => {
  const a = smoothAreaD([0, 50, 100], [10, 20, 30], 200)
  assert.match(a, / L 100 200 L 0 200 Z$/)
  assert.equal(smoothAreaD([10], [10], 200), '')
})

// ── 轴刻度 / 坐标 ─────────────────────────────────────────────────────────────

test('niceCeil 1/2/5×10^k 向上圆整；非法输入回 1', () => {
  assert.equal(niceCeil(7), 10)
  assert.equal(niceCeil(13.31), 20)
  assert.equal(niceCeil(5), 5)
  assert.equal(niceCeil(0.03), 0.05)
  assert.equal(niceCeil(0), 1)
  assert.equal(niceCeil(NaN), 1)
})

test('axisMax 有数据圆整轴顶、全零/非法返回 0（不渲染假刻度）', () => {
  assert.equal(axisMax(90), 100)
  assert.equal(axisMax(0), 0)
  assert.equal(axisMax(-5), 0)
  assert.equal(axisMax(NaN), 0)
})

test('yPixel 0 在底边、轴顶在顶边、超顶夹住、全零轴安全', () => {
  assert.equal(yPixel(0, 100, 10, 180), 190)
  assert.equal(yPixel(100, 100, 10, 180), 10)
  assert.equal(yPixel(250, 100, 10, 180), 10)
  assert.equal(yPixel(0, 0, 10, 180), 190)   // axisMax 0 → 分母保护
})

test('dayXs 单日居中、多日均匀铺满、非法 n 返回空', () => {
  assert.deepEqual(dayXs(1, 40, 600), [340])
  assert.deepEqual(dayXs(3, 40, 600), [40, 340, 640])
  assert.deepEqual(dayXs(0, 40, 600), [])
})

test('nearestIndex 吸附最近日、空/非法安全', () => {
  assert.equal(nearestIndex(120, [0, 100, 200]), 1)
  assert.equal(nearestIndex(160, [0, 100, 200]), 2)
  assert.equal(nearestIndex(NaN, [0, 100]), -1)
  assert.equal(nearestIndex(50, []), -1)
})

// ── buildTrendData / xLabelIndices ───────────────────────────────────────────

test('buildTrendData 输入=hit+miss、maxTokens/maxCost、failclosed 独立、脏数据安全', () => {
  const d = buildTrendData([
    { day: '2026-07-05', cost: 1.5, hit: 800, miss: 200, output: 100, failclosed: 0, users: 2 },
    { day: '2026-07-06', cost: 'junk', hit: 300, miss: 300, output: 20, failclosed: 512000, users: 1 },
  ])
  assert.deepEqual(d.input, [1000, 600])
  assert.deepEqual(d.cost, [1.5, 0])
  assert.equal(d.maxTokens, 1000)
  assert.equal(d.maxCost, 1.5)
  assert.deepEqual(d.failclosed, [0, 512000])   // 不进 input/maxTokens
  assert.deepEqual(buildTrendData(null).days, [])
})

test('xLabelIndices 稀疏含首末、去重升序', () => {
  assert.deepEqual(xLabelIndices(1), [0])
  const idx = xLabelIndices(30)
  assert.equal(idx[0], 0)
  assert.equal(idx[idx.length - 1], 29)
  assert.ok(idx.length <= 4)
  assert.deepEqual(idx, [...new Set(idx)].sort((a, b) => a - b))
})
