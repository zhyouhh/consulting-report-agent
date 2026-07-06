import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import { formatYuan } from '../utils/quotaFormat'
import { capPayload, validateNewPassword } from '../utils/adminApi'
import { normalizeAuthError } from '../utils/authError'
import {
  listDays, aggregateByDay, usageOverview, hitRateLabel, formatTokenCount, filterUsageRows,
} from '../utils/adminUsage'
import { applyTheme, getInitialTheme, toggleTheme } from '../utils/theme'
import { IconSun, IconMoon, IconShield } from './icons'
import UsageTrendChart from './UsageTrendChart'
import SearchPoolQuota from './SearchPoolQuota'

// 管理控制台独立页面（2026-07-06）：/admin 路由整页渲染（原弹窗 AdminPanel 升级而来）。
// 设计沿用主界面海军蓝双主题 token 体系；功能 = 概览统计 + 近 30 日用量趋势/明细
// + 用户管理（额度/改密/禁用）+ 邀请码 + 自定义 API 允许域名。
// 鉴权自理：/api/auth/me 判定 未登录/非管理员/需改密 三种拦截态，不依赖主 App 的状态。

function StatCard({ label, value, hint }) {
  return (
    <div className="bg-card border border-border rounded-card p-4 min-w-0">
      <div className="text-11 text-t3">{label}</div>
      <div className="text-xl font-bold text-text mt-1 font-mono tabular-nums truncate">{value}</div>
      {hint && <div className="text-11 text-t3 mt-1 truncate">{hint}</div>}
    </div>
  )
}

function SectionCard({ title, children, actions }) {
  return (
    <section className="bg-card border border-border rounded-card p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-15 font-semibold text-text">{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}

// 趋势时间范围选项（天）：fetch 一次 90 天数据，切范围只在前端换 days 窗口，不重新请求。
const USAGE_RANGES = [
  { days: 7, label: '近 7 日' },
  { days: 30, label: '近 30 日' },
  { days: 90, label: '近 90 日' },
]

export default function AdminPage() {
  const [theme, setTheme] = useState(getInitialTheme)
  // 初始主题实际由 index.html head 的同步 bootstrap 应用（/admin 与主应用同一 shell，防 FOUC）；
  // 此 effect 是防御性自洽——组件不静默依赖 shell 脚本存在（codex review 建议）。
  useEffect(() => { applyTheme(theme) }, [theme])
  const [authState, setAuthState] = useState('loading') // loading | unauth | forbidden | mustchange | ready
  const [me, setMe] = useState(null)
  const [users, setUsers] = useState([])
  const [invite, setInvite] = useState('')
  const [hosts, setHosts] = useState('')
  const [defaultHosts, setDefaultHosts] = useState([])
  const [usage, setUsage] = useState(null)      // {since, today, rows}
  const [usageFilter, setUsageFilter] = useState('all')   // 用户筛选：联动趋势图 + 明细表
  const [usageRange, setUsageRange] = useState(30)        // 时间范围（天）：联动趋势图 + 明细表
  const [err, setErr] = useState('')
  // 搜索池额度独立取数：tavily 实时查询可能慢（多 key 串行最坏数秒），
  // 不进 reload 的 Promise.all——加载慢/失败都不拖累核心管理数据。
  const [searchQuota, setSearchQuota] = useState(null)
  const [searchQuotaErr, setSearchQuotaErr] = useState('')
  const [searchQuotaBusy, setSearchQuotaBusy] = useState(false)

  useEffect(() => {
    axios.get('/api/auth/me', { skipUnauthedHandler: true })
      .then((r) => {
        setMe(r.data)
        if (r.data?.must_change_password) setAuthState('mustchange')
        else if (!r.data?.is_admin) setAuthState('forbidden')
        else setAuthState('ready')
      })
      .catch(() => setAuthState('unauth'))
  }, [])

  async function reload() {
    try {
      const [u, c, h, g] = await Promise.all([
        axios.get('/api/admin/users'),
        axios.get('/api/admin/invite-code'),
        axios.get('/api/admin/allowed-hosts'),
        axios.get('/api/admin/usage?days=90'),
      ])
      setUsers(u.data)
      setInvite(c.data.invite_code)
      setDefaultHosts([...(h.data.builtin_hosts || []), ...(h.data.env_hosts || [])])
      setHosts((h.data.extra_hosts || []).join('\n'))
      setUsage(g.data)
      setErr('')
    } catch (e) {
      setErr(normalizeAuthError(e, '加载管理数据失败，请刷新重试'))
    }
  }
  useEffect(() => { if (authState === 'ready') reload() }, [authState])

  async function loadSearchQuota(refresh = false) {
    setSearchQuotaBusy(true)
    try {
      const r = await axios.get(`/api/admin/search-quota${refresh ? '?refresh=true' : ''}`)
      setSearchQuota(r.data)
      setSearchQuotaErr('')
    } catch (e) {
      setSearchQuotaErr(normalizeAuthError(e, '读取搜索池额度失败'))
    } finally {
      setSearchQuotaBusy(false)
    }
  }
  useEffect(() => { if (authState === 'ready') loadSearchQuota() }, [authState])

  const days = useMemo(
    () => (usage ? listDays(usage.since, usage.today) : []),
    [usage],
  )
  // 概览卡固定全局近 30 日（全站健康度），不随用户/时间筛选变。
  const overview = useMemo(
    () => usageOverview(aggregateByDay(usage?.rows, days.slice(-30))),
    [usage, days],
  )
  // 趋势图 + 明细表共用筛选：用户（usageFilter）× 时间范围（usageRange）。
  const rangeDays = useMemo(() => days.slice(-usageRange), [days, usageRange])
  const filteredRows = useMemo(() => {
    const rows = usage?.rows || []
    return usageFilter === 'all' ? rows : rows.filter((r) => r?.uid === usageFilter)
  }, [usage, usageFilter])
  const perDay = useMemo(() => aggregateByDay(filteredRows, rangeDays), [filteredRows, rangeDays])
  const detailRows = useMemo(
    () => filterUsageRows(usage?.rows, usageFilter, rangeDays[0]),
    [usage, usageFilter, rangeDays],
  )
  const usageUsers = useMemo(() => {
    const seen = new Map()
    for (const r of usage?.rows || []) {
      if (r?.uid && !seen.has(r.uid)) seen.set(r.uid, r.username || r.uid)
    }
    return [...seen.entries()].map(([uid, username]) => ({ uid, username }))
  }, [usage])

  async function saveHosts() {
    const list = hosts.split('\n').map((s) => s.trim()).filter(Boolean)
    try { await axios.post('/api/admin/allowed-hosts', { hosts: list }); reload() }
    catch (e) { setErr(normalizeAuthError(e, '保存允许域名失败')) }
  }
  async function setCap(uid, input) {
    try { await axios.post(`/api/admin/users/${uid}/cap`, capPayload(input)); reload() }
    catch (e) { setErr(e?.response ? normalizeAuthError(e, '调整额度失败') : (e?.message || '调整额度失败')) }
  }
  async function resetPassword(uid, pw) {
    if (!validateNewPassword(pw)) { setErr('新密码至少 8 位'); return }
    try { await axios.post(`/api/admin/users/${uid}/password`, { new_password: pw }); setErr('') }
    catch (e) { setErr(normalizeAuthError(e, '重置密码失败')) }
  }
  async function toggleDisabled(uid, disabled) {
    try { await axios.post(`/api/admin/users/${uid}/disabled`, { disabled }); reload() }
    catch (e) { setErr(normalizeAuthError(e, '操作失败')) }
  }
  async function rotateInvite() {
    try { const r = await axios.post('/api/admin/invite-code/rotate', {}); setInvite(r.data.invite_code) }
    catch (e) { setErr(normalizeAuthError(e, '轮换失败')) }
  }

  // ── 拦截态（未登录/无权限/需改密）：给出下一步动作，不留死路 ──────────────
  if (authState !== 'ready') {
    const blockText = {
      loading: '正在验证身份…',
      unauth: '请先在主页登录管理员账号，再访问管理控制台。',
      forbidden: '当前账号没有管理员权限。',
      mustchange: '请先回主页完成首次登录改密，再访问管理控制台。',
    }[authState]
    return (
      <div className="min-h-screen bg-bg flex items-center justify-center p-4">
        <div className="bg-card border border-border rounded-win shadow-card p-8 w-[min(420px,calc(100vw-32px))] text-center">
          <div className="flex justify-center text-t3 mb-3"><IconShield size={28} /></div>
          <div className="text-text text-15 font-semibold mb-2">管理控制台</div>
          <div className="text-t2 text-13 mb-5">{blockText}</div>
          {authState !== 'loading' && (
            <a href="/" className="inline-block px-4 py-2 rounded-btn bg-accent text-white text-13 font-medium hover:bg-accent/90">
              返回主页
            </a>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-bg">
      {/* 顶栏 */}
      <header className="sticky top-0 z-10 bg-card border-b border-border">
        <div className="max-w-5xl mx-auto px-4 h-[56px] flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-t2"><IconShield size={18} /></span>
            <h1 className="text-15 font-semibold text-text truncate">管理控制台</h1>
            <span className="text-11 text-t3 truncate">{me?.username}</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setTheme((t) => toggleTheme(t))}
              title="切换主题"
              className="w-[30px] h-[30px] flex items-center justify-center rounded-md text-t3 hover:bg-card2 hover:text-text"
            >
              {theme === 'dark' ? <IconSun size={15} /> : <IconMoon size={15} />}
            </button>
            <a href="/" className="ml-1 px-3 py-1.5 rounded-btn border border-col bg-card2 text-13 text-text hover:bg-track">
              返回应用
            </a>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 py-6 space-y-5">
        {err && (
          <div className="bg-card border border-error/40 text-error rounded-card px-4 py-3 text-13">{err}</div>
        )}

        {/* 概览 */}
        <div className="grid grid-cols-2 min-[640px]:grid-cols-4 gap-3">
          <StatCard label="今日消耗" value={formatYuan(overview.todayCost)} hint={`活跃用户 ${overview.todayActiveUsers}`} />
          <StatCard label="近 7 日消耗" value={formatYuan(overview.last7Cost)} />
          <StatCard label="近 30 日消耗" value={formatYuan(overview.last30Cost)} />
          <StatCard label="注册用户" value={String(users.length)} hint={`已禁用 ${users.filter((u) => u.disabled).length}`} />
        </div>

        {/* 用量趋势：与明细表共用「用户 × 时间范围」筛选 */}
        <SectionCard
          title={`用量趋势 · ${usageFilter === 'all' ? '全部用户' : (usageUsers.find((u) => u.uid === usageFilter)?.username || '')}`}
          actions={
            <select
              value={usageRange}
              onChange={(e) => setUsageRange(Number(e.target.value))}
              aria-label="趋势时间范围"
              className="bg-field border border-border rounded-btn px-2 py-1 text-12 text-text focus:outline-none focus:ring-2 focus:ring-accent"
            >
              {USAGE_RANGES.map((r) => (
                <option key={r.days} value={r.days}>{r.label}</option>
              ))}
            </select>
          }
        >
          {perDay.length ? <UsageTrendChart perDay={perDay} /> : <div className="text-13 text-t3">暂无用量数据</div>}
        </SectionCard>

        {/* 用量明细 */}
        <SectionCard
          title="用量明细"
          actions={
            <select
              value={usageFilter}
              onChange={(e) => setUsageFilter(e.target.value)}
              aria-label="按用户筛选用量明细"
              className="bg-field border border-border rounded-btn px-2 py-1 text-12 text-text focus:outline-none focus:ring-2 focus:ring-accent"
            >
              <option value="all">全部用户</option>
              {usageUsers.map((u) => (
                <option key={u.uid} value={u.uid}>{u.username}</option>
              ))}
            </select>
          }
        >
          <div className="rounded-card border border-border overflow-x-auto">
            <div role="table" aria-label="用量明细" className="min-w-[560px]">
              <div role="row" className="grid grid-cols-[1fr_1.4fr_1fr_1fr_1fr] gap-2 bg-card2 border-b border-border px-[14px] py-[10px] text-11 font-semibold text-t2">
                <span role="columnheader">日期</span>
                <span role="columnheader">用户</span>
                <span role="columnheader" className="text-right">消耗</span>
                <span role="columnheader" className="text-right">缓存命中率</span>
                <span role="columnheader" className="text-right">输出 tokens</span>
              </div>
              {detailRows.length === 0 && (
                <div className="px-[14px] py-4 text-13 text-t3">窗口期内暂无用量记录</div>
              )}
              {detailRows.map((r) => (
                <div
                  key={`${r.day}-${r.uid}`}
                  role="row"
                  className="grid grid-cols-[1fr_1.4fr_1fr_1fr_1fr] gap-2 items-center px-[14px] py-[9px] border-b border-hair last:border-b-0 hover:bg-card2 text-12"
                >
                  <span role="cell" className="text-t2 font-mono">{r.day.slice(5)}</span>
                  <span role="cell" className="text-text truncate">{r.username}</span>
                  <span
                    role="cell"
                    className="text-right text-text font-mono tabular-nums"
                    title={r.failclosed_tokens > 0 ? `含中断估算计费 ${formatTokenCount(r.failclosed_tokens)} tokens（流中断未取到真实 usage）` : undefined}
                  >
                    {formatYuan(r.cost_yuan)}
                    {r.failclosed_tokens > 0 && <span className="text-warn">*</span>}
                  </span>
                  <span role="cell" className="text-right text-t2 font-mono tabular-nums">{hitRateLabel(r.cache_hit_tokens, r.cache_miss_tokens)}</span>
                  <span role="cell" className="text-right text-t2 font-mono tabular-nums">{formatTokenCount(r.output_tokens)}</span>
                </div>
              ))}
            </div>
          </div>
        </SectionCard>

        {/* 搜索池额度：tavily 实时 / brave 被动观测 / serper·exa 本地估算（来源标签见卡片） */}
        <SectionCard
          title="搜索池额度"
          actions={
            <button
              onClick={() => loadSearchQuota(true)}
              disabled={searchQuotaBusy}
              className="text-abright hover:text-accent text-13 disabled:opacity-50"
            >
              {searchQuotaBusy ? '刷新中…' : '刷新'}
            </button>
          }
        >
          <SearchPoolQuota data={searchQuota} error={searchQuotaErr} />
        </SectionCard>

        {/* 用户管理（保留可编辑额度 input——用户硬要求，别改回只读） */}
        <SectionCard title="用户管理">
          <div className="rounded-card border border-border overflow-x-auto">
            <div role="table" aria-label="用户列表" className="min-w-[600px]">
              <div role="row" className="grid grid-cols-[1.6fr_1fr_1fr_1fr_1.3fr] gap-2 bg-card2 border-b border-border px-[14px] py-[10px] text-11 font-semibold text-t2">
                <span role="columnheader">用户</span>
                <span role="columnheader">今日</span>
                <span role="columnheader">额度</span>
                <span role="columnheader">状态</span>
                <span role="columnheader" className="text-right">操作</span>
              </div>
              {users.map((u) => (
                <div
                  key={u.uid}
                  role="row"
                  className="grid grid-cols-[1.6fr_1fr_1fr_1fr_1.3fr] gap-2 items-center px-[14px] py-[11px] border-b border-hair last:border-b-0 hover:bg-card2"
                >
                  <span role="cell" className="text-13 text-text truncate">
                    {u.username}
                    {u.is_admin && <span className="text-t3 ml-1">(admin)</span>}
                  </span>
                  <span role="cell" className="text-12 text-text font-mono tabular-nums">{formatYuan(u.today_cost_yuan)}</span>
                  <span role="cell">
                    <input
                      defaultValue={u.daily_cap_yuan}
                      aria-label={`${u.username} 每日额度`}
                      className="w-16 bg-field border border-border rounded-tag px-1 py-[2px] text-12 font-mono tabular-nums text-text focus:outline-none focus:ring-2 focus:ring-accent"
                      onBlur={(e) => setCap(u.uid, e.target.value)}
                    />
                  </span>
                  <span role="cell" className={`text-xs ${u.disabled ? 'text-warn' : 'text-success'}`}>
                    {u.disabled ? '已禁用' : '正常'}
                  </span>
                  <span role="cell" className="text-right text-12">
                    <button onClick={() => toggleDisabled(u.uid, !u.disabled)} className="text-abright hover:text-accent mr-3">
                      {u.disabled ? '启用' : '禁用'}
                    </button>
                    <button
                      onClick={() => { const pw = prompt('新密码（≥8 位）'); if (pw) resetPassword(u.uid, pw) }}
                      className="text-abright hover:text-accent"
                    >
                      改密
                    </button>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </SectionCard>

        {/* 邀请码 + 允许域名 */}
        <div className="grid grid-cols-1 min-[720px]:grid-cols-2 gap-5">
          <SectionCard title="邀请码">
            <div className="text-sm text-text">
              <code className="font-mono bg-card2 border border-border rounded-tag px-2 py-1">{invite}</code>
              <button onClick={rotateInvite} className="ml-3 text-abright hover:text-accent text-13">轮换</button>
            </div>
            <p className="text-11 text-t3 mt-3">新同事注册需要此邀请码；怀疑外泄时轮换即可，已注册账号不受影响。</p>
          </SectionCard>
          <SectionCard title="自定义 API 允许域名">
            <div className="text-11 text-t3 mb-2">默认内置：{defaultHosts.join('、')}</div>
            <textarea
              value={hosts}
              onChange={(e) => setHosts(e.target.value)}
              rows={3}
              aria-label="额外允许的自定义 API 域名（每行一个）"
              className="w-full bg-field border border-border rounded-btn px-2 py-1 text-13 text-text focus:outline-none focus:ring-2 focus:ring-accent"
              placeholder="my.llm.cn"
            />
            <button onClick={saveHosts} className="mt-2 text-abright hover:text-accent text-13">保存允许域名</button>
          </SectionCard>
        </div>
      </main>
    </div>
  )
}
