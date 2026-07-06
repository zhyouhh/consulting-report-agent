import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  listDays, aggregateByDay, usageOverview, hitRateLabel, formatTokenCount, filterUsageRows,
} from '../src/utils/adminUsage.js'

// ── listDays ─────────────────────────────────────────────────────────────────

test('listDays 闭区间逐日（含跨月）', () => {
  assert.deepEqual(listDays('2026-06-29', '2026-07-02'), [
    '2026-06-29', '2026-06-30', '2026-07-01', '2026-07-02',
  ])
})

test('listDays 同日返回单元素；倒置/坏输入返回空', () => {
  assert.deepEqual(listDays('2026-07-06', '2026-07-06'), ['2026-07-06'])
  assert.deepEqual(listDays('2026-07-07', '2026-07-06'), [])
  assert.deepEqual(listDays('not-a-date', '2026-07-06'), [])
  assert.deepEqual(listDays(undefined, undefined), [])
})

// ── aggregateByDay ───────────────────────────────────────────────────────────

const ROWS = [
  { uid: 'a', username: 'alice', day: '2026-07-05', cost_yuan: 1.5, cache_hit_tokens: 800, cache_miss_tokens: 200, output_tokens: 100 },
  { uid: 'b', username: 'bob', day: '2026-07-05', cost_yuan: 0.5, cache_hit_tokens: 0, cache_miss_tokens: 100, output_tokens: 50 },
  { uid: 'a', username: 'alice', day: '2026-07-06', cost_yuan: 2.0, cache_hit_tokens: 300, cache_miss_tokens: 300, output_tokens: 20 },
]

test('aggregateByDay 按天聚合消耗/tokens/活跃用户数，空日补零', () => {
  const perDay = aggregateByDay(ROWS, ['2026-07-04', '2026-07-05', '2026-07-06'])
  assert.equal(perDay.length, 3)
  assert.deepEqual(perDay[0], { day: '2026-07-04', cost: 0, hit: 0, miss: 0, output: 0, failclosed: 0, users: 0 })
  assert.equal(perDay[1].cost, 2.0)
  assert.equal(perDay[1].users, 2)
  assert.equal(perDay[1].hit, 800)
  assert.equal(perDay[2].cost, 2.0)
  assert.equal(perDay[2].users, 1)
})

test('aggregateByDay 汇总 failclosed_tokens（缺字段安全归零）', () => {
  const perDay = aggregateByDay(
    [
      { uid: 'a', day: '2026-07-06', cost_yuan: 1, failclosed_tokens: 256000 },
      { uid: 'b', day: '2026-07-06', cost_yuan: 1, failclosed_tokens: 100000 },
      { uid: 'c', day: '2026-07-06', cost_yuan: 1 },   // 老 API 无字段
    ],
    ['2026-07-06'],
  )
  assert.equal(perDay[0].failclosed, 356000)
})

test('aggregateByDay 窗口外的行丢弃、非数值安全归零', () => {
  const perDay = aggregateByDay(
    [{ uid: 'x', day: '1999-01-01', cost_yuan: 9 }, { uid: 'a', day: '2026-07-06', cost_yuan: 'oops' }],
    ['2026-07-06'],
  )
  assert.equal(perDay[0].cost, 0)
  assert.equal(perDay[0].users, 1)
})

// ── usageOverview ────────────────────────────────────────────────────────────

test('usageOverview 今日=末位、近7/30日窗口求和', () => {
  const perDay = Array.from({ length: 30 }, (_, i) => ({ day: `d${i}`, cost: 1, users: i === 29 ? 3 : 1 }))
  const o = usageOverview(perDay)
  assert.equal(o.todayCost, 1)
  assert.equal(o.todayActiveUsers, 3)
  assert.equal(o.last7Cost, 7)
  assert.equal(o.last30Cost, 30)
})

test('usageOverview 空输入安全', () => {
  const o = usageOverview([])
  assert.equal(o.todayCost, 0)
  assert.equal(o.todayActiveUsers, 0)
  assert.equal(o.last7Cost, 0)
})

// ── hitRateLabel / formatTokenCount ──────────────────────────────────────────

test('hitRateLabel 常规/零分母', () => {
  assert.equal(hitRateLabel(800, 200), '80%')
  assert.equal(hitRateLabel(0, 0), '—')
  assert.equal(hitRateLabel(null, undefined), '—')
})

test('formatTokenCount 紧凑显示', () => {
  assert.equal(formatTokenCount(0), '0')
  assert.equal(formatTokenCount(999), '999')
  assert.equal(formatTokenCount(15000), '1.5万')
  assert.equal(formatTokenCount('junk'), '0')
})

// ── filterUsageRows ──────────────────────────────────────────────────────────

test('filterUsageRows 按 uid 过滤 + 日期降序', () => {
  const all = filterUsageRows(ROWS, 'all')
  assert.equal(all.length, 3)
  assert.equal(all[0].day, '2026-07-06')
  const onlyA = filterUsageRows(ROWS, 'a')
  assert.deepEqual(onlyA.map((r) => r.day), ['2026-07-06', '2026-07-05'])
})

test('filterUsageRows sinceDay 时间窗（含当日）与 uid 复合过滤', () => {
  assert.deepEqual(filterUsageRows(ROWS, 'all', '2026-07-06').map((r) => r.day), ['2026-07-06'])
  assert.deepEqual(filterUsageRows(ROWS, 'a', '2026-07-05').map((r) => r.day), ['2026-07-06', '2026-07-05'])
  assert.equal(filterUsageRows(ROWS, 'all', '2027-01-01').length, 0)
  assert.equal(filterUsageRows(ROWS, 'all', undefined).length, 3)   // 不给 sinceDay = 不过滤
})

test('filterUsageRows 不改原数组、空输入安全', () => {
  const input = [...ROWS]
  filterUsageRows(input, 'all')
  assert.deepEqual(input, ROWS)
  assert.deepEqual(filterUsageRows(null, 'all'), [])
})
