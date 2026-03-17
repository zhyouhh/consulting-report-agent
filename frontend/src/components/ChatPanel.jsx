import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'

export default function ChatPanel({ project, onTogglePreview }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [tokenUsage, setTokenUsage] = useState(null)
  const messagesEndRef = useRef(null)

  useEffect(() => {
    if (project) {
      setMessages([{
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: '你好！请告诉我你想写什么类型的报告？报告的主题是什么？'
      }])
      setTokenUsage(null)
    }
  }, [project])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async () => {
    if (!input.trim() || !project) return

    const userMsg = { id: `${Date.now()}-${Math.random()}`, role: 'user', content: input }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const res = await axios.post('/api/chat', {
        project_name: project,
        message: input
      })
      setMessages(prev => [...prev, {
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: res.data.content
      }])
      if (res.data.token_usage) {
        setTokenUsage(res.data.token_usage)
      }
    } catch (error) {
      console.error('发送消息失败:', error)
      setMessages(prev => [...prev, {
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: `API调用失败: ${error.response?.data?.detail || error.message}`
      }])
    }
    setLoading(false)
  }

  return (
    <div className="flex-1 flex flex-col bg-[#1a1a2e]">
      <div className="p-4 border-b border-[#2a2a4a] flex justify-between items-center">
        <h2 className="font-semibold text-[#e2e2f0]">{project || '请选择或创建项目'}</h2>
        <button onClick={onTogglePreview} className="text-sm text-[#8888a8] hover:text-[#e2e2f0]">
          切换预览
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-2xl px-4 py-2 rounded-lg ${
              msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-[#252545] text-[#e2e2f0]'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#252545] px-4 py-2 rounded-lg text-[#8888a8]">正在思考...</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {tokenUsage && (
        <div className="px-4 py-2 border-t border-[#2a2a4a] flex items-center gap-2 text-xs text-[#8888a8]">
          <div className="flex-1 h-1.5 bg-[#252545] rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all"
              style={{ width: `${Math.min(100, (tokenUsage.current_tokens / tokenUsage.max_tokens) * 100)}%` }}
            />
          </div>
          <span>{Math.round(tokenUsage.current_tokens / 1000)}k / {Math.round(tokenUsage.max_tokens / 1000)}k</span>
          {tokenUsage.compressed && <span className="text-yellow-500">已压缩</span>}
        </div>
      )}

      <div className="p-4 border-t border-[#2a2a4a]">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                sendMessage()
              }
            }}
            placeholder="输入消息..."
            disabled={!project || loading}
            className="flex-1 bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={sendMessage}
            disabled={!project || loading}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 disabled:bg-[#3a3a5a]"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
