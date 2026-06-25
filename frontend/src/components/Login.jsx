import React, { useState } from 'react'
import axios from 'axios'
import { normalizeAuthError } from '../utils/authError.js'

export default function Login({ onAuthed }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [invite, setInvite] = useState(''); const [err, setErr] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const submit = async (e) => {
    e.preventDefault()
    if (submitting) return            // 防双击重复提交
    // 提交用 trim 后的用户名（与下面的长度校验一致、且避免存入尾随空格用户名）；密码保留原样
    // （空格可能有意义）。客户端先校验长度（短长度即时反馈、不发注定 422 的请求——后端 422 的
    // detail 是数组，若漏到 setErr 会白屏，见 normalizeAuthError）。
    const cleanUsername = username.trim()
    if (cleanUsername.length < 3 || password.length < 6) {
      setErr('用户名至少 3 位、密码至少 6 位')
      return
    }
    setErr(''); setSubmitting(true)
    try {
      if (mode === 'register') await axios.post('/api/auth/register', { username: cleanUsername, password, invite_code: invite })
      await axios.post('/api/auth/login', { username: cleanUsername, password })
      onAuthed((await axios.get('/api/auth/me')).data)   // 成功后 App 卸载本组件，无需复位 submitting
    } catch (e2) {
      // normalizeAuthError 恒返回字符串（含 422 数组 detail）→ 绝不把对象塞进 React 子节点致白屏。
      setErr(normalizeAuthError(e2)); setSubmitting(false)
    }
  }
  return (
    <div className="flex items-center justify-center h-screen bg-bg">
      <div className="w-[344px]">
        {/* wordmark 行 */}
        <div className="flex items-center justify-center gap-[11px] mb-[22px]">
          <div className="w-[34px] h-[34px] rounded-[9px] bg-accent text-white flex items-center justify-center text-base font-bold">R</div>
          <span className="text-lg font-bold text-text tracking-tight">咨询报告助手</span>
        </div>
        {/* form 卡片 */}
        <form onSubmit={submit} className="bg-card border border-border rounded-win p-6 shadow-popover">
          <h1 className="text-base font-semibold text-text mb-4">{mode === 'login' ? '登录' : '注册'}</h1>
          <input
            className="w-full h-10 px-3 rounded-btn border border-border bg-field text-text text-15 placeholder-t3 mb-[11px]"
            placeholder="用户名"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="password"
            className="w-full h-10 px-3 rounded-btn border border-border bg-field text-text text-15 placeholder-t3 mb-[11px]"
            placeholder="密码"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {mode === 'register' && (
            <input
              className="w-full h-10 px-3 rounded-btn border border-border bg-field text-text text-15 placeholder-t3 mb-[11px]"
              placeholder="邀请码"
              value={invite}
              onChange={(e) => setInvite(e.target.value)}
            />
          )}
          {err && <div className="text-error text-12 mb-[11px]">{err}</div>}
          <button
            type="submit"
            disabled={submitting}
            className="w-full h-10 rounded-btn bg-accent text-white text-15 font-medium mb-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {submitting ? '处理中…' : (mode === 'login' ? '登录' : '注册并登录')}
          </button>
          <button
            type="button"
            className="w-full text-t3 text-13"
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? '没有账号？去注册' : '已有账号？去登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
