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
      <div className="bg-card border border-border rounded-win p-6 w-[680px] max-h-[80vh] overflow-auto shadow-popover" onClick={(e) => e.stopPropagation()}>
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
        <table className="w-full text-sm text-text">
          <thead><tr className="text-t3"><th>用户</th><th>今日</th><th>额度</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.uid}>
                <td>{u.username}{u.is_admin ? ' (admin)' : ''}</td>
                <td>{formatYuan(u.today_cost_yuan)}</td>
                <td>
                  <input defaultValue={u.daily_cap_yuan} className="w-16 bg-field border border-border rounded-tag px-1 focus:outline-none focus:ring-2 focus:ring-accent"
                         onBlur={(e) => setCap(u.uid, e.target.value)} />
                </td>
                <td>{u.disabled ? '已禁用' : '正常'}</td>
                <td>
                  <button onClick={() => toggleDisabled(u.uid, !u.disabled)} className="text-abright hover:text-accent mr-2">
                    {u.disabled ? '启用' : '禁用'}
                  </button>
                  <button onClick={() => { const pw = prompt('新密码（≥8 位）'); if (pw) resetPassword(u.uid, pw) }}
                          className="text-abright hover:text-accent">改密</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
