// 管理控制台·用量趋势折线图的纯函数层（2026-07-06 柱状图→平滑折线重做）。
// 与 UsageTrendChart 渲染解耦：node:test 直接可测（无 jsdom），组件只做摆放与交互。

// 单调三次插值（Fritsch–Carlson）切线：曲线圆滑且不过冲——零值日不会画出负值假谷、
// 峰值日不会画出比真实值更高的假峰（数据可视化诚实性 > 视觉花哨）。
// 假设 x 均匀间隔（逐日序列恒成立），切线单位 = y/格。
export function monotoneTangents(ys) {
  const n = ys.length
  if (n < 2) return ys.map(() => 0)
  const d = []
  for (let i = 0; i < n - 1; i++) d.push(ys[i + 1] - ys[i])
  const m = [d[0]]
  for (let i = 1; i < n - 1; i++) m.push(d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2)
  m.push(d[n - 2])
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue }
    const a = m[i] / d[i]
    const b = m[i + 1] / d[i]
    const s = a * a + b * b
    if (s > 9) {
      const t = 3 / Math.sqrt(s)
      m[i] = t * a * d[i]
      m[i + 1] = t * b * d[i]
    }
  }
  return m
}

const fmt = (v) => String(Math.round(v * 100) / 100)

// 像素坐标序列 → SVG 平滑路径（Hermite 切线转三次贝塞尔）。点数 0 → ''；1 → 单点 M。
export function smoothPathD(xs, ys) {
  const n = Math.min(xs.length, ys.length)
  if (n === 0) return ''
  if (n === 1) return `M ${fmt(xs[0])} ${fmt(ys[0])}`
  const m = monotoneTangents(ys.slice(0, n))
  let d = `M ${fmt(xs[0])} ${fmt(ys[0])}`
  for (let i = 0; i < n - 1; i++) {
    const h = xs[i + 1] - xs[i]
    d += ` C ${fmt(xs[i] + h / 3)} ${fmt(ys[i] + m[i] / 3)}, ${fmt(xs[i + 1] - h / 3)} ${fmt(ys[i + 1] - m[i + 1] / 3)}, ${fmt(xs[i + 1])} ${fmt(ys[i + 1])}`
  }
  return d
}

// 线路径闭合到底边成面积路径（用于「缓存命中」下方的浅色填充）。
export function smoothAreaD(xs, ys, baselineY) {
  const line = smoothPathD(xs, ys)
  if (!line || xs.length < 2) return ''
  return `${line} L ${fmt(xs[xs.length - 1])} ${fmt(baselineY)} L ${fmt(xs[0])} ${fmt(baselineY)} Z`
}

// 「好看的」轴顶：1/2/5×10^k 向上圆整。
export function niceCeil(value) {
  const v = Number(value)
  if (!Number.isFinite(v) || v <= 0) return 1
  const exp = Math.floor(Math.log10(v))
  const f = v / 10 ** exp
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10
  return nf * 10 ** exp
}

// 轴顶：有数据 → niceCeil 圆整；全零序列 → 0（调用方只画基线 0 标签，不渲染假刻度）。
export function axisMax(maxValue) {
  const v = Number(maxValue)
  return Number.isFinite(v) && v > 0 ? niceCeil(v) : 0
}

// 数据值 → y 像素（0 在底边、axisMax 在顶边；超轴顶夹住）。
export function yPixel(value, axisMax, plotTop, plotHeight) {
  const v = Math.max(0, Number(value) || 0)
  const m = Number(axisMax) > 0 ? Number(axisMax) : 1
  return plotTop + plotHeight * (1 - Math.min(1, v / m))
}

// n 个日槽的均匀 x 坐标；单日居中。
export function dayXs(n, plotLeft, plotWidth) {
  if (!Number.isInteger(n) || n <= 0) return []
  if (n === 1) return [plotLeft + plotWidth / 2]
  const step = plotWidth / (n - 1)
  return Array.from({ length: n }, (_, i) => plotLeft + i * step)
}

// 指针 x → 最近日 index；空/非法输入 → -1。
export function nearestIndex(px, xs) {
  if (!Array.isArray(xs) || xs.length === 0 || !Number.isFinite(px)) return -1
  let best = 0
  let bd = Infinity
  for (let i = 0; i < xs.length; i++) {
    const d = Math.abs(xs[i] - px)
    if (d < bd) { bd = d; best = i }
  }
  return best
}

// perDay（aggregateByDay 产物）→ 图表序列。输入 = hit+miss（真实 prompt tokens，
// 不含 fail-closed 估算）；failclosed 只进 tooltip 提示，不画线。
export function buildTrendData(perDay) {
  const list = Array.isArray(perDay) ? perDay : []
  const num = (v) => (Number.isFinite(Number(v)) ? Math.max(0, Number(v)) : 0)
  const cost = list.map((d) => num(d?.cost))
  const hit = list.map((d) => num(d?.hit))
  const input = list.map((d) => num(d?.hit) + num(d?.miss))
  const output = list.map((d) => num(d?.output))
  return {
    days: list.map((d) => d?.day ?? ''),
    cost,
    hit,
    input,
    output,
    failclosed: list.map((d) => num(d?.failclosed)),
    users: list.map((d) => num(d?.users)),
    maxCost: Math.max(0, ...cost),
    maxTokens: Math.max(0, ...input, ...output),   // hit ≤ input 恒真，无需参与
  }
}

// x 轴日期标签的稀疏 index（≤4 个、含首末、去重）。
export function xLabelIndices(n) {
  if (!Number.isInteger(n) || n <= 0) return []
  if (n === 1) return [0]
  const picks = [0, Math.round((n - 1) / 3), Math.round(((n - 1) * 2) / 3), n - 1]
  return [...new Set(picks)].sort((a, b) => a - b)
}
