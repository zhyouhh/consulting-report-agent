import { useEffect, useState } from 'react'
import axios from 'axios'
import { formatYuan } from '../utils/quotaFormat'
import { capPayload, validateNewPassword } from '../utils/adminApi'

export default function AdminPanel({ onClose }) {
  const [users, setUsers] = useState([])
  const [invite, setInvite] = useState('')
  const [hosts, setHosts] = useState('')          // 允许域名（每行一个，含默认只读 + extra 可编辑）
  const [defaultHosts, setDefaultHosts] = useState([])
  const [err, setErr] = useState('')

  async function reload() {
    try {
      const [u, c, h] = await Promise.all([
        axios.get('/api/admin/users'),
        axios.get('/api/admin/invite-code'),
        axios.get('/api/admin/allowed-hosts'),
      ])
      setUsers(u.data); setInvite(c.data.invite_code)
      setDefaultHosts([...(h.data.builtin_hosts || []), ...(h.data.env_hosts || [])])
      setHosts((h.data.extra_hosts || []).join('\n'))
    } catch (e) { setErr('加载失败') }
  }
  useEffect(() => { reload() }, [])

  async function saveHosts() {
    const list = hosts.split('\n').map((s) => s.trim()).filter(Boolean)
    try { await axios.post('/api/admin/allowed-hosts', { hosts: list }); reload() }
    catch (e) { setErr('保存允许域名失败') }
  }

  async function setCap(uid, input) {
    try { await axios.post(`/api/admin/users/${uid}/cap`, capPayload(input)); reload() }
    catch (e) { setErr(e?.message || '调整额度失败') }
  }
  async function resetPassword(uid, pw) {
    if (!validateNewPassword(pw)) { setErr('新密码至少 8 位'); return }
    try { await axios.post(`/api/admin/users/${uid}/password`, { new_password: pw }); setErr('') }
    catch (e) { setErr('重置密码失败') }
  }
  async function toggleDisabled(uid, disabled) {
    try { await axios.post(`/api/admin/users/${uid}/disabled`, { disabled }); reload() }
    catch (e) { setErr('操作失败') }
  }
  async function rotateInvite() {
    try { const r = await axios.post('/api/admin/invite-code/rotate', {}); setInvite(r.data.invite_code) }
    catch (e) { setErr('轮换失败') }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60" onClick={onClose}>
      <div className="bg-card border border-border rounded-win p-6 w-[min(680px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto shadow-popover" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between mb-4">
          <h2 className="text-text font-semibold">用户管理</h2>
          <button onClick={onClose} className="text-t2 hover:text-text">关闭</button>
        </div>
        {err && <div className="text-error text-sm mb-2">{err}</div>}
        <div className="mb-4 text-sm text-text">
          邀请码：<code className="font-mono">{invite}</code>
          <button onClick={rotateInvite} className="ml-3 text-abright hover:text-accent">轮换</button>
        </div>
        <div className="mb-4 text-sm text-text">
          <div className="mb-1">自定义 API 允许域名（每行一个，同事要用别的服务在这里加）：</div>
          <div className="text-11 text-t3 mb-1">默认内置：{defaultHosts.join('、')}</div>
          <textarea value={hosts} onChange={(e) => setHosts(e.target.value)} rows={3}
                    className="w-full bg-field border border-border rounded-btn px-2 py-1 text-text focus:outline-none focus:ring-2 focus:ring-accent" placeholder="my.llm.cn" />
          <button onClick={saveHosts} className="mt-1 text-abright hover:text-accent">保存允许域名</button>
        </div>
        {/* 原型是 div grid 排版；补 ARIA table 语义让屏幕阅读器拿到列头/单元格关系（codex NIT） */}
        <div className="rounded-card border border-border overflow-x-auto">
          <div role="table" aria-label="用户列表" className="overflow-hidden min-w-[600px]">
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
                {/* 原型这列是只读文本；保留可编辑 input（用户要求），样式收进等宽小框 */}
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
      </div>
    </div>
  )
}
