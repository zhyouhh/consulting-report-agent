// 管理控制台·历史用量的纯函数层（2026-07-06 /admin 独立页面）。
// 与 AdminPage 渲染解耦：node:test 直接可测（无 jsdom），组件只做取数与摆放。

// 'YYYY-MM-DD' 闭区间逐日列表（字符串日界与后端 usage_daily.day 一致，字典序 == 日期序）。
export function listDays(since, today) {
  const out = []
  const start = Date.parse(`${since}T00:00:00Z`)
  const end = Date.parse(`${today}T00:00:00Z`)
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end) return out
  for (let t = start; t <= end && out.length < 400; t += 86400000) {
    out.push(new Date(t).toISOString().slice(0, 10))
  }
  return out
}

// 按天聚合后端 rows（行粒度 = uid×day）：总消耗/命中/未命中/输出/中断估算 + 当天活跃用户数。
// days 之外的行丢弃（窗口由 days 列表控制——时间范围切换 = 换 days，行集不动）。
export function aggregateByDay(rows, days) {
  const map = new Map(days.map((d) => [d, { day: d, cost: 0, hit: 0, miss: 0, output: 0, failclosed: 0, users: 0 }]))
  for (const r of Array.isArray(rows) ? rows : []) {
    const slot = map.get(r?.day)
    if (!slot) continue
    slot.cost += Number(r.cost_yuan) || 0
    slot.hit += Number(r.cache_hit_tokens) || 0
    slot.miss += Number(r.cache_miss_tokens) || 0
    slot.output += Number(r.output_tokens) || 0
    slot.failclosed += Number(r.failclosed_tokens) || 0
    slot.users += 1
  }
  return days.map((d) => map.get(d))
}

// 概览统计：今日消耗/今日活跃用户/近7日/近30日（perDay 按天升序，末位 = today）。
export function usageOverview(perDay) {
  const list = Array.isArray(perDay) ? perDay : []
  const sum = (xs) => xs.reduce((a, d) => a + (Number(d?.cost) || 0), 0)
  const today = list[list.length - 1] ?? null
  return {
    todayCost: today ? today.cost : 0,
    todayActiveUsers: today ? today.users : 0,
    last7Cost: sum(list.slice(-7)),
    last30Cost: sum(list.slice(-30)),
  }
}

// 缓存命中率标签：hit/(hit+miss)，无 token 记录显示 '—'。
export function hitRateLabel(hit, miss) {
  const h = Number(hit) || 0
  const m = Number(miss) || 0
  const total = h + m
  if (total <= 0) return '—'
  return `${Math.round((h / total) * 100)}%`
}

// token 数紧凑显示：≥1 万用「N.N万」，其余原样；非法输入回 '0'。
export function formatTokenCount(n) {
  const v = Number(n)
  if (!Number.isFinite(v) || v <= 0) return '0'
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`
  return String(Math.round(v))
}

// 明细行过滤 + 排序（日期降序、同日按用户名升序）；uid 为空/'all' 时不过滤；
// sinceDay 给定时只留 day >= sinceDay（'YYYY-MM-DD' 字典序 == 日期序，与后端 usage_daily.day 一致）。
export function filterUsageRows(rows, uid, sinceDay) {
  const list = Array.isArray(rows) ? rows.slice() : []
  const byUser = uid && uid !== 'all' ? list.filter((r) => r?.uid === uid) : list
  const filtered = sinceDay ? byUser.filter((r) => typeof r?.day === 'string' && r.day >= sinceDay) : byUser
  return filtered.sort((a, b) => {
    if (a.day !== b.day) return a.day < b.day ? 1 : -1
    return String(a.username || '').localeCompare(String(b.username || ''))
  })
}
