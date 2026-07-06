// 管理控制台·搜索池额度的纯函数层（与 SearchPoolQuota 渲染解耦，node:test 直测）。
// 数据源见后端 backend/search_quota.py：tavily 实时 / brave 被动观测 / serper·exa 本地估算，
// 前端职责 = 中文文案 + 数值格式化 + 趋势序列构建，不做任何额度语义推断。

export const PROVIDER_ORDER = ['tavily', 'brave', 'serper', 'exa']

const PROVIDER_TITLES = {
  tavily: 'Tavily',
  brave: 'Brave',
  serper: 'Serper',
  exa: 'Exa',
}

export function providerTitle(name) {
  return PROVIDER_TITLES[name] || String(name || '')
}

// 数据来源标签 + 诚实性提示（估算类必须说明口径，不能装作精确值）
const SOURCE_META = {
  live: { label: '实时', hint: '来自 provider 官方用量接口（约 5 分钟缓存）' },
  observed: { label: '被动观测', hint: '来自最近一次搜索响应头，随使用自动更新' },
  estimated: { label: '本地估算', hint: '按本服务记账估算，不含其它部署的消耗，实际剩余可能更少' },
  none: { label: '未声明', hint: '未配置额度信息，仅显示调用统计' },
}

export function sourceMeta(source) {
  return SOURCE_META[source] || SOURCE_META.none
}

export function layerLabel(layer) {
  if (layer === 'primary') return '主力'
  if (layer === 'secondary') return '兜底'
  return '未参与路由'
}

export function quotaModelLabel(model) {
  if (model === 'monthly') return '每月重置'
  if (model === 'one_time') return '一次性额度'
  return ''
}

// 额度数值格式化：usd 两位小数带 $，其余取整；null/非法 → '—'
export function formatQuotaValue(value, unit) {
  const v = Number(value)
  if (value == null || !Number.isFinite(v)) return '—'
  if (unit === 'usd') return `$${v.toFixed(2)}`
  return String(Math.round(v))
}

// 剩余占比（进度条）：恒 [0,1]，非法输入 → 0
export function remainingRatio(remaining, total) {
  const r = Number(remaining)
  const t = Number(total)
  if (!Number.isFinite(r) || !Number.isFinite(t) || t <= 0) return 0
  return Math.min(1, Math.max(0, r / t))
}

// 后端 history（[{day, provider, calls, units, errors}]）→ 逐日多序列。
// days 由调用方给全窗口（listDays(since, today)），缺数据的日补 0，序列按 PROVIDER_ORDER
// 排序、history 里出现但不在 ORDER 的排后面（前端不丢后端给的任何 provider）。
export function buildSearchTrend(history, days) {
  const dayList = Array.isArray(days) ? days : []
  const rows = Array.isArray(history) ? history : []
  const dayIndex = new Map(dayList.map((d, i) => [d, i]))
  const seriesMap = new Map()
  for (const row of rows) {
    const name = row?.provider
    const idx = dayIndex.get(row?.day)
    if (!name || idx == null) continue
    if (!seriesMap.has(name)) seriesMap.set(name, dayList.map(() => 0))
    seriesMap.get(name)[idx] += Number(row.calls) || 0
  }
  const names = [...seriesMap.keys()].sort((a, b) => {
    const ia = PROVIDER_ORDER.indexOf(a)
    const ib = PROVIDER_ORDER.indexOf(b)
    return (ia === -1 ? PROVIDER_ORDER.length : ia) - (ib === -1 ? PROVIDER_ORDER.length : ib)
      || String(a).localeCompare(String(b))
  })
  const series = names.map((name) => ({ name, calls: seriesMap.get(name) }))
  const maxCalls = Math.max(0, ...series.flatMap((s) => s.calls))
  return { days: dayList, series, maxCalls }
}

// provider 卡排序：按 PROVIDER_ORDER，未知的排后
export function sortProviders(providers) {
  const list = Array.isArray(providers) ? providers.slice() : []
  return list.sort((a, b) => {
    const ia = PROVIDER_ORDER.indexOf(a?.name)
    const ib = PROVIDER_ORDER.indexOf(b?.name)
    return (ia === -1 ? PROVIDER_ORDER.length : ia) - (ib === -1 ? PROVIDER_ORDER.length : ib)
  })
}

// 观测时间戳（秒）→ 相对时间短文案；非法 → ''
export function observedAgoLabel(observedAtSeconds, nowMs) {
  const t = Number(observedAtSeconds)
  const now = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now()
  if (!Number.isFinite(t) || t <= 0) return ''
  const diff = Math.max(0, now / 1000 - t)
  if (diff < 90) return '刚刚'
  if (diff < 3600) return `${Math.round(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时前`
  return `${Math.round(diff / 86400)} 天前`
}
