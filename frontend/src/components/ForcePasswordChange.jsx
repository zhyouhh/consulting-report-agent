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
    <div className="h-screen flex items-center justify-center bg-[#0f0f23]">
      <div className="bg-[#15162d] rounded-xl p-6 w-[360px]">
        <h2 className="text-[#e2e2f0] font-semibold mb-3">首次登录请修改密码</h2>
        {err && <div className="text-red-400 text-sm mb-2">{err}</div>}
        <input type="password" placeholder="原密码" value={oldPw}
               onChange={(e) => setOldPw(e.target.value)} className="w-full mb-2 bg-[#0f0f23] px-2 py-1 text-[#e2e2f0]" />
        <input type="password" placeholder="新密码（≥8 位）" value={newPw}
               onChange={(e) => setNewPw(e.target.value)} className="w-full mb-3 bg-[#0f0f23] px-2 py-1 text-[#e2e2f0]" />
        <button onClick={submit} className="w-full bg-[#64ffda] text-[#0f0f23] rounded py-1">确认修改</button>
      </div>
    </div>
  )
}
