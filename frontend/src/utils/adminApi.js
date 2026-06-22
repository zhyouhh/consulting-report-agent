import { quotaRatio } from './quotaFormat.js'

export function capPayload(input) {
  const raw = (input ?? '').toString().trim()
  if (raw === '') return { daily_cost_yuan: null }
  const n = Number(raw)
  if (!Number.isFinite(n) || n < 0) throw new Error('额度必须是非负数字')
  return { daily_cost_yuan: raw }   // 字符串送后端（AdminCapBody: str|None → Decimal 解析，免 float 漂移）
}

export function validateNewPassword(pw) {
  return typeof pw === 'string' && pw.length >= 8
}

export function summarizeUser(u) {
  return {
    ...u,
    ratio: quotaRatio(u?.today_cost_yuan ?? 0, u?.daily_cap_yuan ?? 0),
  }
}
