import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'

export default function ChatPanel({ project, onTogglePreview }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [tokenUsage, setTokenUsage] = useState(null)
  const [abortController, setAbortController] = useState(null)
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

  const clearConversation = async () => {
    if (!confirm('确定要清空对话历史吗？')) return
    try {
      await axios.delete(`/api/projects/${project}/conversation`)
      setMessages([{
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: '对话已清空。请告诉我你想写什么类型的报告？'
      }])
      setTokenUsage(null)
    } catch (error) {
      alert('清空失败: ' + (error.response?.data?.detail || error.message))
    }
  }

  const stopGeneration = () => {
    if (abortController) {
      abortController.abort()
      setLoading(false)
    }
  }

  const copyMessage = (content) => {
    navigator.clipboard.writeText(content).then(() => {
      // 简单提示，不打断用户
    }).catch(() => {
      alert('复制失败，请手动选择文本')
    })
  }

  const sendMessage = async () => {
    if (!input.trim() || !project) return

    const userMsg = { id: `${Date.now()}-${Math.random()}`, role: 'user', content: input }
    setMessages(prev => [...prev, userMsg])
    const userInput = input
    setInput('')
    setLoading(true)

    const controller = new AbortController()
    setAbortController(controller)

    // 创建助手消息占位
    const assistantId = `${Date.now()}-${Math.random()}`
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }])

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_name: project, message: userInput }),
        signal: controller.signal
      })

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') break

            try {
              const parsed = JSON.parse(data)
              if (parsed.type === 'content') {
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: m.content + parsed.data } : m
                ))
              } else if (parsed.type === 'usage') {
                setTokenUsage(parsed.data)
              } else if (parsed.type === 'error') {
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: `错误: ${parsed.data}` } : m
                ))
              }
            } catch (e) {
              console.error('解析SSE失败:', e)
            }
          }
        }
      }
    } catch (error) {
      if (error.name === 'AbortError') {
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, content: m.content || '已停止生成' } : m
        ))
      } else {
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, content: `API调用失败: ${error.message}` } : m
        ))
      }
    }
    setLoading(false)
    setAbortController(null)
  }

  return (
    <div className="flex-1 flex flex-col bg-[#1a1a2e]">
      <div className="p-4 border-b border-[#2a2a4a] flex justify-between items-center">
        <h2 className="font-semibold text-[#e2e2f0]">{project || '请选择或创建项目'}</h2>
        <div className="flex gap-2">
          {project && (
            <button onClick={clearConversation} className="text-sm text-[#8888a8] hover:text-[#e2e2f0]">
              清空对话
            </button>
          )}
          <button onClick={onTogglePreview} className="text-sm text-[#8888a8] hover:text-[#e2e2f0]">
            切换预览
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-2xl px-4 py-2 rounded-lg relative group ${
              msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-[#252545] text-[#e2e2f0]'
            }`}>
              <div className="whitespace-pre-wrap">{msg.content}</div>
              <button
                onClick={() => copyMessage(msg.content)}
                className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 text-xs px-2 py-1 bg-[#1a1a2e] rounded hover:bg-[#2a2a4a] transition-opacity"
                title="复制"
              >
                复制
              </button>
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
          {loading ? (
            <button
              onClick={stopGeneration}
              className="bg-red-600 text-white px-6 py-2 rounded-lg hover:bg-red-700"
            >
              停止
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!project}
              className="bg-blue-600 text-white px-6 py-2 rounded-lg hover:bg-blue-700 disabled:bg-[#3a3a5a]"
            >
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
