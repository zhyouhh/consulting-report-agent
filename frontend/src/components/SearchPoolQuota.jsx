import { useEffect, useMemo, useRef, useState } from 'react'
import {
  buildSearchTrend, formatQuotaValue, layerLabel, observedAgoLabel,
  providerTitle, quotaModelLabel, remainingRatio, sortProviders, sourceMeta,
} from '../utils/searchQuota'
import { listDays } from '../utils/adminUsage'
import { dayXs, smoothPathD, axisMax, yPixel, nearestIndex, xLabelIndices } from '../utils/usageChart'

// 管理控制台·搜索池额度板块：每 provider 一张卡（剩余/来源/调用统计/逐 key 明细）
// + 近 31 日各渠道调用趋势折线。数据来自 GET /api/admin/search-quota（AdminPage 取数）。
// 三种数据质量（实时/被动观测/本地估算）在卡片上明示，估算类带口径提示——不装精确。

const CHART_HEIGHT = 180
const PAD = { top: 12, right: 16, bottom: 24, left: 40 }
const TOOLTIP_WIDTH = 168

// 序列颜色三份写死（stroke-/bg-/fill-）——Tailwind JIT 按源码字面量扫描，禁运行时拼类名。
const SERIES_STYLE = {
  tavily: { stroke: 'stroke-accent', swatch: 'bg-accent', dot: 'fill-accent' },
  brave: { stroke: 'stroke-success', swatch: 'bg-success', dot: 'fill-success' },
  serper: { stroke: 'stroke-warn', swatch: 'bg-warn', dot: 'fill-warn' },
  exa: { stroke: 'stroke-abright', swatch: 'bg-abright', dot: 'fill-abright' },
}
const DEFAULT_SERIES_STYLE = { stroke: 'stroke-t3', swatch: 'bg-t3', dot: 'fill-t3' }

function seriesStyle(name) {
  return SERIES_STYLE[name] || DEFAULT_SERIES_STYLE
}

function barToneClass(ratio) {
  if (ratio <= 0.15) return 'bg-error'
  if (ratio <= 0.4) return 'bg-warn'
  return 'bg-success'
}

function KeyRow({ row, unit, source }) {
  let detail
  if (row.error) {
    detail = <span className="text-warn">查询失败</span>
  } else if (source === 'live' && row.limit != null) {
    detail = (
      <span className="font-mono tabular-nums">
        {formatQuotaValue(row.used, unit)}/{formatQuotaValue(row.limit, unit)}
      </span>
    )
  } else if (source === 'observed' && row.remaining != null) {
    const ago = observedAgoLabel(row.observed_at)
    detail = (
      <span className="font-mono tabular-nums">
        剩余 {formatQuotaValue(row.remaining, unit)}
        {ago && <span className="text-t3 font-sans">（{ago}）</span>}
      </span>
    )
  } else {
    detail = <span className="font-mono tabular-nums">{Math.round(row.observed_calls || 0)} 次调用</span>
  }
  return (
    <div className="flex items-center justify-between gap-2 text-11 text-t2">
      <span className="font-mono text-t3">{row.label}</span>
      {detail}
    </div>
  )
}

function ProviderCard({ provider }) {
  const meta = sourceMeta(provider.source)
  const unit = provider.quota_unit
  const hasQuota = provider.total_remaining != null && provider.total_quota != null
  const ratio = remainingRatio(provider.total_remaining, provider.total_quota)
  const modelText = quotaModelLabel(provider.quota_model)
  return (
    <div className="bg-card2 border border-border rounded-card p-4 min-w-0 space-y-2">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-13 font-semibold text-text">{providerTitle(provider.name)}</span>
        <span className="text-2xs px-1.5 py-[1px] rounded-tag border border-col text-t2">
          {layerLabel(provider.layer)} · 权重 {provider.weight}
        </span>
        <span className="text-2xs px-1.5 py-[1px] rounded-tag bg-asoft text-abright" title={meta.hint}>
          {meta.label}
        </span>
        {!provider.enabled && <span className="text-2xs text-warn">已停用</span>}
      </div>

      {hasQuota ? (
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-15 font-bold text-text font-mono tabular-nums">
              {formatQuotaValue(provider.total_remaining, unit)}
              <span className="text-11 font-normal text-t3"> / {formatQuotaValue(provider.total_quota, unit)}</span>
            </span>
            <span className="text-11 text-t3">
              剩余{provider.source === 'estimated' ? '（估算）' : ''}{modelText ? ` · ${modelText}` : ''}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-track overflow-hidden mt-1.5">
            <div
              className={`h-full rounded-full ${barToneClass(ratio)}`}
              style={{ width: `${Math.round(ratio * 100)}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="text-12 text-t3">未声明额度，仅统计调用</div>
      )}

      <div className="text-11 text-t3">
        今日 {Math.round(provider.observed_calls_today || 0)} 次 · 近 30 日{' '}
        {Math.round(provider.observed_calls_30d || 0)} 次
        {provider.observed_errors_30d > 0 && (
          <span className="text-warn"> · 失败 {Math.round(provider.observed_errors_30d)} 次</span>
        )}
      </div>

      {(provider.keys || []).length > 1 || provider.source === 'live' ? (
        <div className="space-y-1 pt-1 border-t border-hair">
          {(provider.keys || []).map((row) => (
            <KeyRow key={row.label} row={row} unit={unit} source={provider.source} />
          ))}
        </div>
      ) : null}

      {provider.source === 'estimated' && (
        <div className="text-2xs text-t3">{meta.hint}</div>
      )}
    </div>
  )
}

function SearchTrendChart({ trend }) {
  const wrapRef = useRef(null)
  const [width, setWidth] = useState(0)
  const [active, setActive] = useState(null)

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return undefined
    const update = () => setWidth(el.getBoundingClientRect().width)
    update()
    if (typeof ResizeObserver !== 'undefined') {
      const ro = new ResizeObserver(update)
      ro.observe(el)
      return () => ro.disconnect()
    }
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  const n = trend.days.length
  const plot = {
    left: PAD.left,
    top: PAD.top,
    width: Math.max(0, width - PAD.left - PAD.right),
    height: CHART_HEIGHT - PAD.top - PAD.bottom,
  }
  const xs = useMemo(() => dayXs(n, plot.left, plot.width), [n, plot.left, plot.width])
  const top = axisMax(trend.maxCalls)
  const baseline = plot.top + plot.height
  const seriesYs = useMemo(
    () => trend.series.map((s) => s.calls.map((v) => yPixel(v, top || 1, plot.top, plot.height))),
    [trend, top, plot.top, plot.height],
  )

  if (n === 0 || trend.series.length === 0) {
    return <div className="text-13 text-t3">暂无调用记录</div>
  }

  const pickDay = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const idx = nearestIndex(e.clientX - rect.left, xs)
    if (idx >= 0) setActive(idx)
  }
  const activeIdx = active != null && active < n ? active : null
  const tooltipLeft = activeIdx == null
    ? 0
    : Math.min(Math.max(xs[activeIdx] + 10, 4), Math.max(4, width - TOOLTIP_WIDTH - 4))

  return (
    <div ref={wrapRef} className="relative" role="img" aria-label={`近 ${n} 日各渠道搜索调用趋势`}>
      {width > 0 && (
        <svg
          width={width}
          height={CHART_HEIGHT}
          className="block cursor-crosshair select-none"
          onPointerMove={pickDay}
          onClick={pickDay}
          onPointerLeave={() => setActive(null)}
        >
          {[0, 1, 2, 3].map((i) => {
            const y = plot.top + plot.height * (1 - i / 3)
            return (
              <g key={`tick-${i}`}>
                <line x1={plot.left} x2={plot.left + plot.width} y1={y} y2={y} className="stroke-hair" strokeWidth="1" />
                {(top > 0 || i === 0) && (
                  <text x={plot.left - 6} y={y + 3} textAnchor="end" fontSize="10" className="fill-t3 font-mono">
                    {Math.round((top * i) / 3)}
                  </text>
                )}
              </g>
            )
          })}

          {trend.series.map((s, si) => (
            <path
              key={s.name}
              d={smoothPathD(xs, seriesYs[si])}
              fill="none"
              strokeWidth="1.5"
              strokeLinecap="round"
              className={seriesStyle(s.name).stroke}
            />
          ))}

          {xLabelIndices(n).map((i) => (
            <text
              key={`x-${trend.days[i]}`}
              x={xs[i]}
              y={CHART_HEIGHT - 8}
              textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
              fontSize="10"
              className="fill-t3 font-mono"
            >
              {trend.days[i]?.slice(5)}
            </text>
          ))}

          {activeIdx != null && (
            <g>
              <line x1={xs[activeIdx]} x2={xs[activeIdx]} y1={plot.top} y2={baseline} className="stroke-col" strokeWidth="1" />
              {trend.series.map((s, si) => (
                <circle key={`dot-${s.name}`} cx={xs[activeIdx]} cy={seriesYs[si][activeIdx]} r="3" className={seriesStyle(s.name).dot} />
              ))}
            </g>
          )}
        </svg>
      )}

      {activeIdx != null && (
        <div
          className="absolute top-2 z-10 bg-card border border-border rounded-btn shadow-popover px-3 py-2 pointer-events-none"
          style={{ left: tooltipLeft, width: TOOLTIP_WIDTH }}
        >
          <div className="text-11 font-semibold text-text font-mono">{trend.days[activeIdx]}</div>
          <div className="mt-1 space-y-0.5 text-11 text-t2">
            {trend.series.map((s) => (
              <div key={`row-${s.name}`} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 min-w-0">
                  <span className={`inline-block w-2.5 h-[2px] rounded ${seriesStyle(s.name).swatch}`} />
                  {providerTitle(s.name)}
                </span>
                <span className="font-mono tabular-nums text-text">{Math.round(s.calls[activeIdx])}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1 text-11 text-t2">
        {trend.series.map((s) => (
          <span key={`legend-${s.name}`} className="flex items-center gap-1.5">
            <span className={`inline-block w-3.5 h-[2px] rounded ${seriesStyle(s.name).swatch}`} />
            {providerTitle(s.name)}
          </span>
        ))}
      </div>
    </div>
  )
}

export default function SearchPoolQuota({ data, error }) {
  const trend = useMemo(() => {
    if (!data?.configured) return { days: [], series: [], maxCalls: 0 }
    return buildSearchTrend(data.history, listDays(data.since, data.today))
  }, [data])

  if (error) {
    return <div className="text-13 text-error">{error}</div>
  }
  if (!data) {
    return <div className="text-13 text-t3">正在读取搜索池额度…</div>
  }
  if (!data.configured) {
    return <div className="text-13 text-t3">未配置搜索池（缺 managed_search_pool.json），无额度数据。</div>
  }

  const providers = sortProviders(data.providers)
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 min-[640px]:grid-cols-2 gap-3">
        {providers.map((p) => (
          <ProviderCard key={p.name} provider={p} />
        ))}
      </div>
      <SearchTrendChart trend={trend} />
    </div>
  )
}
