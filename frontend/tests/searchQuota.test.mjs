import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  buildSearchTrend, formatQuotaValue, layerLabel, observedAgoLabel, providerTitle,
  quotaModelLabel, remainingRatio, sortProviders, sourceMeta,
} from '../src/utils/searchQuota.js'

test('providerTitle：已知渠道给品牌名，未知回显原名', () => {
  assert.equal(providerTitle('tavily'), 'Tavily')
  assert.equal(providerTitle('serper'), 'Serper')
  assert.equal(providerTitle('other'), 'other')
  assert.equal(providerTitle(null), '')
})

test('sourceMeta：四种来源都有标签+口径提示，未知回退 none', () => {
  assert.equal(sourceMeta('live').label, '官方额度')
  assert.match(sourceMeta('live').hint, /滞后|延迟|非实时|即时/)   // 诚实性守护：不得再宣称「实时」
  assert.equal(sourceMeta('observed').label, '被动观测')
  assert.equal(sourceMeta('estimated').label, '本地估算')
  assert.match(sourceMeta('estimated').hint, /不含其它部署/)
  assert.equal(sourceMeta('whatever').label, '未声明')
})

test('layerLabel / quotaModelLabel 中文文案', () => {
  assert.equal(layerLabel('primary'), '主力')
  assert.equal(layerLabel('secondary'), '兜底')
  assert.equal(layerLabel('unrouted'), '未参与路由')
  assert.equal(quotaModelLabel('monthly'), '每月重置')
  assert.equal(quotaModelLabel('one_time'), '一次性额度')
  assert.equal(quotaModelLabel(''), '')
})

test('formatQuotaValue：usd 两位小数带 $、其余取整、null/非法给 —', () => {
  assert.equal(formatQuotaValue(8, 'usd'), '$8.00')
  assert.equal(formatQuotaValue(2488.4, 'credits'), '2488')
  assert.equal(formatQuotaValue(null, 'credits'), '—')
  assert.equal(formatQuotaValue('abc', 'usd'), '—')
})

test('remainingRatio：恒 [0,1]，非法/零总量给 0', () => {
  assert.equal(remainingRatio(800, 1000), 0.8)
  assert.equal(remainingRatio(2000, 1000), 1)
  assert.equal(remainingRatio(-1, 1000), 0)
  assert.equal(remainingRatio(5, 0), 0)
  assert.equal(remainingRatio(NaN, 1000), 0)
})

test('buildSearchTrend：按日对齐补零、多 key 同日合并已在后端、序列按固定渠道序', () => {
  const days = ['2026-07-05', '2026-07-06', '2026-07-07']
  const history = [
    { day: '2026-07-06', provider: 'serper', calls: 4, units: 4, errors: 0 },
    { day: '2026-07-07', provider: 'tavily', calls: 2, units: 2, errors: 0 },
    { day: '2026-07-04', provider: 'exa', calls: 9, units: 9, errors: 0 },   // 窗外日丢弃
  ]
  const trend = buildSearchTrend(history, days)
  assert.deepEqual(trend.days, days)
  assert.deepEqual(trend.series.map((s) => s.name), ['tavily', 'serper'])
  assert.deepEqual(trend.series[0].calls, [0, 0, 2])
  assert.deepEqual(trend.series[1].calls, [0, 4, 0])
  assert.equal(trend.maxCalls, 4)
})

test('buildSearchTrend：空输入不炸', () => {
  const trend = buildSearchTrend(null, null)
  assert.deepEqual(trend.days, [])
  assert.deepEqual(trend.series, [])
  assert.equal(trend.maxCalls, 0)
})

test('sortProviders：tavily/brave/serper/exa 固定序，未知排后', () => {
  const sorted = sortProviders([
    { name: 'exa' }, { name: 'zzz' }, { name: 'tavily' }, { name: 'serper' },
  ])
  assert.deepEqual(sorted.map((p) => p.name), ['tavily', 'serper', 'exa', 'zzz'])
})

test('observedAgoLabel：相对时间分档 + 非法输入给空串', () => {
  const now = 1_800_000_000_000   // ms
  assert.equal(observedAgoLabel(now / 1000 - 30, now), '刚刚')
  assert.equal(observedAgoLabel(now / 1000 - 600, now), '10 分钟前')
  assert.equal(observedAgoLabel(now / 1000 - 7200, now), '2 小时前')
  assert.equal(observedAgoLabel(now / 1000 - 172800, now), '2 天前')
  assert.equal(observedAgoLabel(null, now), '')
  assert.equal(observedAgoLabel(-5, now), '')
})
