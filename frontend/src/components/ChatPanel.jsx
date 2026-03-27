import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import { showError, showSuccess } from '../utils/toast'
import { buildChatRequest, toggleMaterialSelection } from '../utils/chatMaterials'
import {
  buildProjectWelcomeMessage,
  shouldFlushStreamingQueueImmediately,
  splitAssistantMessageBlocks,
  takeStreamingTextSlice,
} from '../utils/chatPresentation'
import { describeConnectionMode } from '../utils/connectionMode'
import { supportsImageAttachments } from '../utils/modelCapabilities'
import { summarizeWorkspace } from '../utils/workspaceSummary'

export default function ChatPanel({
  projectId,
  project,
  settings,
  workspace,
  materials,
  onMaterialsMerged,
  onProjectMutated,
  onTogglePreview,
}) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [selectedMaterialIds, setSelectedMaterialIds] = useState([])
  const [tokenUsage, setTokenUsage] = useState(null)
  const [abortController, setAbortController] = useState(null)
  const messagesEndRef = useRef(null)
  const uploadInputRef = useRef(null)
  const pendingContentRef = useRef(new Map())
  const contentFlushTimersRef = useRef(new Map())
  const connection = describeConnectionMode(settings || {})
  const workspaceSummary = summarizeWorkspace(workspace || {})
  const selectedMaterials = materials.filter(material => selectedMaterialIds.includes(material.id))
  const canSendImages = supportsImageAttachments(settings)

  useEffect(() => {
    setSelectedMaterialIds([])

    if (projectId) {
      // 加载历史对话
      axios.get(`/api/projects/${encodeURIComponent(projectId)}/conversation`)
        .then(res => {
          const history = res.data.messages || []
          if (history.length > 0) {
            // 过滤掉 system/tool 消息，只显示 user/assistant
            const displayMessages = history
              .filter(m => m.role === 'user' || m.role === 'assistant')
              .map((m, i) => ({
                id: `${Date.now()}-${i}`,
                role: m.role,
                content: m.content,
                attachedMaterialIds: m.attached_material_ids || [],
              }))
            setMessages(displayMessages)
          } else {
            // 没有历史，显示欢迎消息
            setMessages([{
              id: `${Date.now()}-${Math.random()}`,
              role: 'assistant',
              content: buildProjectWelcomeMessage(project || {})
            }])
          }
        })
        .catch(() => {
          // 加载失败，显示欢迎消息
          setMessages([{
            id: `${Date.now()}-${Math.random()}`,
            role: 'assistant',
            content: buildProjectWelcomeMessage(project || {})
          }])
        })
      setTokenUsage(null)
    } else {
      setMessages([])
    }
  }, [projectId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => () => {
    contentFlushTimersRef.current.forEach(timerId => clearInterval(timerId))
    contentFlushTimersRef.current.clear()
    pendingContentRef.current.clear()
  }, [])

  const clearStreamingQueue = (assistantId) => {
    const timerId = contentFlushTimersRef.current.get(assistantId)
    if (timerId) {
      clearInterval(timerId)
      contentFlushTimersRef.current.delete(assistantId)
    }
    pendingContentRef.current.delete(assistantId)
  }

  const flushStreamingQueueImmediately = (assistantId) => {
    const pending = pendingContentRef.current.get(assistantId) || ''
    if (pending) {
      setMessages(prev => prev.map(message =>
        message.id === assistantId ? { ...message, content: message.content + pending } : message
      ))
    }
    clearStreamingQueue(assistantId)
  }

  const enqueueAssistantContent = (assistantId, chunkText) => {
    const currentPending = pendingContentRef.current.get(assistantId) || ''
    pendingContentRef.current.set(assistantId, currentPending + chunkText)

    if (contentFlushTimersRef.current.has(assistantId)) {
      return
    }

    const timerId = window.setInterval(() => {
      const pending = pendingContentRef.current.get(assistantId) || ''
      if (!pending) {
        clearStreamingQueue(assistantId)
        return
      }

      const { emitted, remaining } = takeStreamingTextSlice(pending, 8)
      pendingContentRef.current.set(assistantId, remaining)
      setMessages(prev => prev.map(message =>
        message.id === assistantId ? { ...message, content: message.content + emitted } : message
      ))

      if (!remaining) {
        clearStreamingQueue(assistantId)
      }
    }, 24)

    contentFlushTimersRef.current.set(assistantId, timerId)
  }

  const clearConversation = async () => {
    if (!confirm('确定要清空对话历史吗？')) return
    try {
      await axios.delete(`/api/projects/${encodeURIComponent(projectId)}/conversation`)
      setMessages([{
        id: `${Date.now()}-${Math.random()}`,
        role: 'assistant',
        content: buildProjectWelcomeMessage(project || {})
      }])
      setTokenUsage(null)
      onProjectMutated?.()
    } catch (error) {
      showError('清空失败: ' + (error.response?.data?.detail || error.message))
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
      showError('复制失败，请手动选择文本')
    })
  }

  const uploadFiles = async (files) => {
    if (!files.length || !projectId) {
      return
    }

    const formData = new FormData()
    files.forEach(file => formData.append('files', file))

    setUploading(true)
    try {
      const res = await axios.post(
        `/api/projects/${encodeURIComponent(projectId)}/materials/upload`,
        formData,
      )
      const uploadedMaterials = res.data.materials || []
      if (uploadedMaterials.length > 0) {
        onMaterialsMerged?.(uploadedMaterials)
        setSelectedMaterialIds(prev => [
          ...prev,
          ...uploadedMaterials
            .map(material => material.id)
            .filter(materialId => !prev.includes(materialId)),
        ])
        showSuccess(`已导入 ${uploadedMaterials.length} 份材料`)
        onProjectMutated?.()
      }
    } catch (error) {
      showError('上传材料失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      setUploading(false)
    }
  }

  const sendMessage = async () => {
    if (!input.trim() || !projectId || uploading) return
    if (selectedMaterials.some(material => material.media_kind === 'image_like') && !canSendImages) {
      showError('当前模型不支持图片输入，请切换模型或取消选择图片材料')
      return
    }

    const userMsg = {
      id: `${Date.now()}-${Math.random()}`,
      role: 'user',
      content: input.trim(),
      attachedMaterialIds: selectedMaterialIds,
    }
    setMessages(prev => [...prev, userMsg])
    const userInput = input
    const attachedMaterialIds = selectedMaterialIds
    setInput('')
    setSelectedMaterialIds([])
    setLoading(true)

    const controller = new AbortController()
    setAbortController(controller)

    // 创建助手消息占位
    const assistantId = `${Date.now()}-${Math.random()}`
    clearStreamingQueue(assistantId)
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }])

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildChatRequest({
          projectId,
          messageText: userInput,
          attachedMaterialIds,
        })),
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
                enqueueAssistantContent(assistantId, parsed.data)
              } else if (parsed.type === 'tool') {
                if (shouldFlushStreamingQueueImmediately('tool')) {
                  flushStreamingQueueImmediately(assistantId)
                }
                // 显示工具调用信息
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: m.content + '\n' + parsed.data } : m
                ))
              } else if (parsed.type === 'usage') {
                setTokenUsage(parsed.data)
              } else if (parsed.type === 'error') {
                if (shouldFlushStreamingQueueImmediately('error')) {
                  flushStreamingQueueImmediately(assistantId)
                }
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
        if (shouldFlushStreamingQueueImmediately('abort')) {
          flushStreamingQueueImmediately(assistantId)
        }
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, content: m.content || '已停止生成' } : m
        ))
      } else {
        if (shouldFlushStreamingQueueImmediately('error')) {
          flushStreamingQueueImmediately(assistantId)
        }
        setMessages(prev => prev.map(m =>
          m.id === assistantId ? { ...m, content: `API调用失败: ${error.message}` } : m
        ))
      }
    }
    setLoading(false)
    setAbortController(null)
    onProjectMutated?.()
  }

  const handleSelectFiles = async (event) => {
    const files = Array.from(event.target.files || [])
    await uploadFiles(files)
    event.target.value = ''
  }

  const handleDragOver = (event) => {
    event.preventDefault()
    if (projectId && !loading && !uploading) {
      setDragActive(true)
    }
  }

  const handleDragLeave = (event) => {
    event.preventDefault()
    setDragActive(false)
  }

  const handleDrop = async (event) => {
    event.preventDefault()
    setDragActive(false)
    const files = Array.from(event.dataTransfer?.files || [])
    await uploadFiles(files)
  }

  return (
    <div className="flex-1 flex flex-col bg-[#1a1a2e]">
      <div className="p-4 border-b border-[#2a2a4a] flex justify-between items-center">
        <div>
          <h2 className="font-semibold text-[#e2e2f0]">{project?.name || '请选择或创建项目'}</h2>
          {projectId && (
            <p className="text-xs text-[#8888a8] mt-1">
              {connection.title} · 当前阶段 {workspaceSummary.stageLabel}
            </p>
          )}
        </div>
        <div className="flex gap-2">
          {projectId && (
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
        {messages.map((msg) => {
          const assistantBlocks = msg.role === 'assistant'
            ? splitAssistantMessageBlocks(msg.content)
            : [{ type: 'text', content: msg.content }]

          return (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-2xl px-4 py-2 rounded-lg relative group ${
                msg.role === 'user' ? 'bg-blue-600 text-white' : 'bg-[#252545] text-[#e2e2f0]'
              }`}>
                {msg.attachedMaterialIds?.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {msg.attachedMaterialIds.map(materialId => {
                      const attachedMaterial = materials.find(material => material.id === materialId)
                      return (
                        <span key={materialId} className="text-[11px] px-2 py-1 rounded-full bg-[#1a1a2e] border border-[#3a3a5a] text-[#b8bbe8]">
                          {attachedMaterial?.display_name || materialId}
                        </span>
                      )
                    })}
                  </div>
                )}
                {msg.role === 'assistant' ? (
                  <div className="space-y-2">
                    {assistantBlocks.map((block, index) => block.type === 'tool' ? (
                      <div key={index} className="text-xs bg-[#1a1a2e] px-2 py-1 rounded border border-[#3a3a5a] text-[#8888a8] font-mono">
                        {block.content}
                      </div>
                    ) : (
                      <ReactMarkdown key={index} className="prose prose-invert prose-sm max-w-none">
                        {block.content}
                      </ReactMarkdown>
                    ))}
                  </div>
                ) : (
                  <div className="whitespace-pre-wrap">{msg.content}</div>
                )}

                <button
                  onClick={() => copyMessage(msg.content)}
                  className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 text-xs px-2 py-1 bg-[#1a1a2e] rounded hover:bg-[#2a2a4a] transition-opacity"
                  title="复制"
                >
                  复制
                </button>
              </div>
            </div>
          )
        })}
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

      <div
        className={`p-4 border-t border-[#2a2a4a] ${dragActive ? 'bg-[#20284f]' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {selectedMaterials.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {selectedMaterials.map(material => (
              <button
                key={material.id}
                type="button"
                onClick={() => setSelectedMaterialIds(prev => toggleMaterialSelection(prev, material.id))}
                className="text-xs px-2 py-1 rounded-full bg-[#23234a] border border-[#3a3a5a] text-[#d6d8f6]"
              >
                {material.display_name} ×
              </button>
            ))}
          </div>
        )}
        {materials.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2">
            {materials.map(material => (
              <button
                key={material.id}
                type="button"
                onClick={() => setSelectedMaterialIds(prev => toggleMaterialSelection(prev, material.id))}
                className={`text-xs px-2 py-1 rounded-full border ${
                  selectedMaterialIds.includes(material.id)
                    ? 'bg-blue-600 border-blue-500 text-white'
                    : 'bg-[#15162d] border-[#2f3158] text-[#b6b8de]'
                }`}
              >
                {material.display_name}
              </button>
            ))}
          </div>
        )}
        {!canSendImages && materials.some(material => material.media_kind === 'image_like') && (
          <div className="mb-3 text-xs text-[#f5b16a]">
            当前自定义模型按保守规则视为不支持图片输入，选中图片材料时会阻止发送。
          </div>
        )}
        {dragActive && (
          <div className="mb-3 rounded border border-dashed border-[#6d8cff] px-3 py-2 text-sm text-[#d9e2ff]">
            松开鼠标即可导入材料并附带到本轮消息
          </div>
        )}
        <div className="flex gap-2">
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleSelectFiles}
          />
          <button
            type="button"
            onClick={() => uploadInputRef.current?.click()}
            disabled={!projectId || loading || uploading}
            className="border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded-lg hover:bg-[#222244] disabled:bg-[#20203a] disabled:text-[#77789a]"
            title="导入新材料"
          >
            {uploading ? '上传中...' : '+'}
          </button>
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
            disabled={!projectId || loading || uploading}
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
              disabled={!projectId || uploading}
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
