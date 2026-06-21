import React, { useState } from 'react'
import axios from 'axios'

export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [invite, setInvite] = useState(''); const [err, setErr] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    if (submitting) return            // 防双击重复提交
    setErr(''); setSubmitting(true)
    try {
      if (mode === 'register') await axios.post('/api/auth/register', { username, password, invite_code: invite })
      await axios.post('/api/auth/login', { username, password })
      onAuthed((await axios.get('/api/auth/me')).data)   // 成功后 App 卸载本组件，无需复位 submitting
    } catch (e2) {
      setErr(e2?.response?.data?.detail || '操作失败，请重试'); setSubmitting(false)
    }
  }
  return (
    <div className="flex items-center justify-center h-screen bg-[#0f0f23]">
      <form onSubmit={submit} className="bg-[#1a1a2e] p-8 rounded-xl w-80 border border-[#2a2a4a]">
        <h1 className="text-lg font-semibold text-[#e2e2f0] mb-4">咨询报告助手 · {mode === 'login' ? '登录' : '注册'}</h1>
        <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="密码" value={password} onChange={(e) => setPassword(e.target.value)} />
        {mode === 'register' && <input className="w-full mb-3 px-3 py-2 rounded bg-[#15162d] text-[#e2e2f0]" placeholder="邀请码" value={invite} onChange={(e) => setInvite(e.target.value)} />}
        {err && <div className="text-red-400 text-sm mb-3">{err}</div>}
        <button type="submit" disabled={submitting} className="w-full bg-blue-600 text-white py-2 rounded mb-2 disabled:opacity-60 disabled:cursor-not-allowed">{submitting ? '处理中…' : (mode === 'login' ? '登录' : '注册并登录')}</button>
        <button type="button" className="w-full text-[#8888a8] text-sm" onClick={() => setMode(mode === 'login' ? 'register' : 'login')}>
          {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  )
}
