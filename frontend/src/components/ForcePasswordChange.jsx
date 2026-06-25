import { useState } from 'react'
import axios from 'axios'
import { validateNewPassword } from '../utils/adminApi'

export default function ForcePasswordChange({ onChanged }) {
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [err, setErr] = useState('')
  async function submit() {
    if (!validateNewPassword(newPw)) { setErr('新密码至少 8 位'); return }
    try {
      await axios.post('/api/auth/change-password', { old_password: oldPw, new_password: newPw })
      onChanged?.()
    } catch (e) { setErr('修改失败，请检查原密码') }
  }
  return (
    <div className="h-screen flex items-center justify-center bg-bg">
      <div className="w-[360px] bg-card border border-border rounded-win p-6 shadow-[0_18px_50px_rgba(0,0,0,0.12)]">
        <h2 className="text-base font-semibold text-text mb-[5px]">首次登录请修改密码</h2>
        <p className="text-12 text-t3 mb-4">为了账户安全，请设置一个新的登录密码。</p>
        {err && <div className="text-error text-12 mb-[11px]">{err}</div>}
        <input
          type="password"
          placeholder="原密码"
          value={oldPw}
          onChange={(e) => setOldPw(e.target.value)}
          className="w-full h-10 px-3 rounded-btn border border-border bg-field text-text text-15 placeholder-t3 mb-[11px]"
        />
        <input
          type="password"
          placeholder="新密码（≥8 位）"
          value={newPw}
          onChange={(e) => setNewPw(e.target.value)}
          className="w-full h-10 px-3 rounded-btn border border-border bg-field text-text text-15 placeholder-t3 mb-4"
        />
        <button
          onClick={submit}
          className="w-full h-10 rounded-btn bg-accent text-white text-15 font-medium"
        >
          确认修改
        </button>
      </div>
    </div>
  )
}
