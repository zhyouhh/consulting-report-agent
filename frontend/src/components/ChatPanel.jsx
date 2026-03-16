import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'

export default function ChatPanel({ project, onTogglePreview }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef(null)

  useEffect(() => {
    if (project) {
      setMessages([{
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: '你好！请告诉我你想写什么类型的报告？报告的主题是什么？'
      }])
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
      setMessages(prev => [...prev, { id: `${Date.now()}-${Math.random()}`, role: 'assistant', content: res.data.content }])
    } catch (error) {
      console.error('发送消息失败:', error)
      setMessages(prev => [...prev, {
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: '抱歉，发生了错误。请检查网络连接后重试。'
      }])
    }
    setLoading(false)
  }

  return (
    <div className="flex-1 flex flex-col bg-white">
      <div className="p-4 border-b border-gray-200 flex justify-between items-center">
        <h2 className="font-semibold text-gray-800">{project || '请选择或创建项目'}</h2>
        <button onClick={onTogglePreview} className="text-sm text-gray-600 hover:text-gray-800">
          切换预览
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-2xl px-4 py-2 rounded-lg ${
              msg.role === 'user' ? 'bg-blue-500 text-white' : 'bg-gray-100 text-gray-800'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="bg-gray-100 px-4 py-2 rounded-lg text-gray-600">正在思考...</div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t border-gray-200">
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
            className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500"
          />
          <button
            onClick={sendMessage}
            disabled={!project || loading}
            className="bg-blue-500 text-white px-6 py-2 rounded-lg hover:bg-blue-600 disabled:bg-gray-300"
          >
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
