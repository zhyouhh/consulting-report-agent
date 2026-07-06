import { useEffect, useMemo, useRef, useState } from 'react'
import { formatYuan } from '../utils/quotaFormat'
import { hitRateLabel, formatTokenCount } from '../utils/adminUsage'
import {
  buildTrendData, dayXs, smoothPathD, smoothAreaD, axisMax, yPixel, nearestIndex, xLabelIndices,
} from '../utils/usageChart'

// 管理控制台·用量趋势平滑折线图（2026-07-06 柱状图重做）。
// 纯 SVG + 主题 token（stroke-*/fill-* 语义类，无图表库、无裸 hex）；坐标/路径数学在
// utils/usageChart.js（node:test 直测）。双轴：tokens 左轴（输入/缓存命中/输出）、¥ 右轴（消耗）。
// hover / 点击任意位置 → 吸附最近日期，竖线 + 数值卡片。

const CHART_HEIGHT = 224
const PAD = { top: 12, right: 52, bottom: 24, left: 52 }
const TOOLTIP_WIDTH = 172

// 序列样式：消耗 = accent 虚线走右轴；tokens 三序列走左轴，缓存命中带浅色面积。
// stroke/swatch/dot 三份类名都写死——Tailwind JIT 按源码字面量扫描，运行时拼接的类不会进产物。
const SERIES = [
  { key: 'input', label: '输入', axis: 'tokens', stroke: 'stroke-warn', swatch: 'bg-warn', dot: 'fill-warn' },
  { key: 'hit', label: '缓存命中', axis: 'tokens', stroke: 'stroke-success', swatch: 'bg-success', dot: 'fill-success', area: true },
  { key: 'output', label: '输出', axis: 'tokens', stroke: 'stroke-abright', swatch: 'bg-abright', dot: 'fill-abright' },
  { key: 'cost', label: '消耗', axis: 'cost', stroke: 'stroke-accent', swatch: 'bg-accent', dot: 'fill-accent', dash: '5 4' },
]

export default function UsageTrendChart({ perDay }) {
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

  const data = useMemo(() => buildTrendData(perDay), [perDay])
  const n = data.days.length

  const plot = {
    left: PAD.left,
    top: PAD.top,
    width: Math.max(0, width - PAD.left - PAD.right),
    height: CHART_HEIGHT - PAD.top - PAD.bottom,
  }
  const xs = useMemo(() => dayXs(n, plot.left, plot.width), [n, plot.left, plot.width])
  // 轴顶 0 = 该侧全零：只标基线 0，不渲染凭空的假刻度（Codex NIT）。
  const tokenMax = axisMax(data.maxTokens)
  const costMax = axisMax(data.maxCost)
  const baseline = plot.top + plot.height

  const seriesYs = useMemo(() => {
    const out = {}
    for (const s of SERIES) {
      const top = s.axis === 'cost' ? costMax : tokenMax
      out[s.key] = data[s.key].map((v) => yPixel(v, top || 1, plot.top, plot.height))
    }
    return out
  }, [data, costMax, tokenMax, plot.top, plot.height])

  if (n === 0) return null

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
    <div ref={wrapRef} className="relative" role="img" aria-label={`近 ${n} 日用量趋势折线图`}>
      {width > 0 && (
        <svg
          width={width}
          height={CHART_HEIGHT}
          className="block cursor-crosshair select-none"
          onPointerMove={pickDay}
          onClick={pickDay}
          onPointerLeave={() => setActive(null)}
        >
          {/* 横向网格线 + 左轴 tokens / 右轴 ¥ 刻度（该侧全零时只标基线 0） */}
          {[0, 1, 2, 3].map((i) => {
            const y = plot.top + plot.height * (1 - i / 3)
            return (
              <g key={`tick-${i}`}>
                <line x1={plot.left} x2={plot.left + plot.width} y1={y} y2={y} className="stroke-hair" strokeWidth="1" />
                {(tokenMax > 0 || i === 0) && (
                  <text x={plot.left - 6} y={y + 3} textAnchor="end" fontSize="10" className="fill-t3 font-mono">
                    {formatTokenCount((tokenMax * i) / 3)}
                  </text>
                )}
                {(costMax > 0 || i === 0) && (
                  <text x={plot.left + plot.width + 6} y={y + 3} textAnchor="start" fontSize="10" className="fill-t3 font-mono">
                    {`¥${Math.round(((costMax * i) / 3) * 100) / 100}`}
                  </text>
                )}
              </g>
            )
          })}

          {/* 缓存命中面积（浅色，视觉锚定缓存健康度） */}
          <path d={smoothAreaD(xs, seriesYs.hit, baseline)} className="fill-success" fillOpacity="0.08" stroke="none" />

          {/* 各序列平滑折线 */}
          {SERIES.map((s) => (
            <path
              key={s.key}
              d={smoothPathD(xs, seriesYs[s.key])}
              fill="none"
              strokeWidth={s.axis === 'cost' ? 2 : 1.5}
              strokeDasharray={s.dash}
              strokeLinecap="round"
              className={s.stroke}
            />
          ))}

          {/* x 轴日期标签（稀疏、含首末） */}
          {xLabelIndices(n).map((i) => (
            <text
              key={`x-${data.days[i]}`}
              x={xs[i]}
              y={CHART_HEIGHT - 8}
              textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}
              fontSize="10"
              className="fill-t3 font-mono"
            >
              {data.days[i]?.slice(5)}
            </text>
          ))}

          {/* 选中日：竖向参考线 + 各序列圆点 */}
          {activeIdx != null && (
            <g>
              <line x1={xs[activeIdx]} x2={xs[activeIdx]} y1={plot.top} y2={baseline} className="stroke-col" strokeWidth="1" />
              {SERIES.map((s) => (
                <circle
                  key={`dot-${s.key}`}
                  cx={xs[activeIdx]}
                  cy={seriesYs[s.key][activeIdx]}
                  r="3"
                  className={s.dot}
                />
              ))}
            </g>
          )}
        </svg>
      )}

      {/* 数值卡片：跟随选中日，边缘自动收拢 */}
      {activeIdx != null && (
        <div
          className="absolute top-2 z-10 bg-card border border-border rounded-btn shadow-popover px-3 py-2 pointer-events-none"
          style={{ left: tooltipLeft, width: TOOLTIP_WIDTH }}
        >
          <div className="text-11 font-semibold text-text font-mono">{data.days[activeIdx]}</div>
          <div className="mt-1 space-y-0.5 text-11 text-t2">
            {SERIES.map((s) => (
              <div key={`row-${s.key}`} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 min-w-0">
                  <span className={`inline-block w-2.5 h-[2px] rounded ${s.swatch}`} />
                  {s.label}
                </span>
                <span className="font-mono tabular-nums text-text">
                  {s.axis === 'cost' ? formatYuan(data.cost[activeIdx]) : formatTokenCount(data[s.key][activeIdx])}
                </span>
              </div>
            ))}
            <div className="flex items-center justify-between gap-2 pt-0.5 border-t border-hair">
              <span>命中率</span>
              <span className="font-mono tabular-nums text-text">
                {hitRateLabel(data.hit[activeIdx], data.input[activeIdx] - data.hit[activeIdx])}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span>活跃用户</span>
              <span className="font-mono tabular-nums text-text">{data.users[activeIdx]}</span>
            </div>
            {data.failclosed[activeIdx] > 0 && (
              <div className="text-2xs text-warn">含中断估算 {formatTokenCount(data.failclosed[activeIdx])} tokens</div>
            )}
          </div>
        </div>
      )}

      {/* 图例 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1 text-11 text-t2">
        {SERIES.map((s) => (
          <span key={`legend-${s.key}`} className="flex items-center gap-1.5">
            <span className={`inline-block w-3.5 h-[2px] rounded ${s.swatch}`} />
            {s.label}
            {s.axis === 'cost' && <span className="text-t3">（右轴 ¥）</span>}
          </span>
        ))}
      </div>
    </div>
  )
}
