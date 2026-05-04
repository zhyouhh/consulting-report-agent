import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import { showError, showInfo, showSuccess } from '../utils/toast'
import { buildChatRequest, toggleMaterialSelection } from '../utils/chatMaterials'
import {
  appendToolEventContent,
  buildProjectWelcomeMessage,
  extractSseDataPayload,
  getStreamResponseError,
  sanitizeAssistantMessage,
  shouldRenderSystemNoticeMessage,
  shouldContinueSseStream,
  shouldFlushStreamingQueueImmediately,
  splitAssistantMessageBlocks,
  takeStreamingTextSlice,
} from '../utils/chatPresentation'
import { shouldSubmitComposerKeydown } from '../utils/composerInputBehavior'
import { describeConnectionMode } from '../utils/connectionMode'
import { formatContextUsage, getContextUsagePercent } from '../utils/contextUsage'
import { supportsImageAttachments } from '../utils/modelCapabilities'
import {
  buildPendingAttachment,
  fileToDataUrl,
  mergePendingAttachments,
  removePendingAttachment,
  splitPendingAttachments,
} from '../utils/pendingAttachments'
import { shouldApplyProjectResponse } from '../utils/projectRequestOwnership'
import { summarizeWorkspace } from '../utils/workspaceSummary'

export default function ChatPanel({
  projectId,
  project,
  settings,
  workspace,
  materials,
  onMaterialsMerged,
  onProjectMutated,
  onToggleWorkspacePanel,
  injectedPrompt,
  onInjectedPromptConsumed,
}) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [isComposing, setIsComposing] = useState(false)
  const [pendingAttachments, setPendingAttachments] = useState([])
  const [selectedMaterialIds, setSelectedMaterialIds] = useState([])
  const [tokenUsage, setTokenUsage] = useState(null)
  const [abortController, setAbortController] = useState(null)
  const messagesEndRef = useRef(null)
  const uploadInputRef = useRef(null)
  const composerInputRef = useRef(null)
  const activeProjectIdRef = useRef(projectId)
  const previousProjectIdRef = useRef(projectId)
  const abortControllerRef = useRef(null)
  const pendingAttachmentsRef = useRef([])
  const pendingContentRef = useRef(new Map())
  const contentFlushTimersRef = useRef(new Map())
  const connection = describeConnectionMode(settings || {})
  const workspaceSummary = summarizeWorkspace(workspace || {})
  const selectedMaterials = materials.filter(material => selectedMaterialIds.includes(material.id))
  const canSendImages = supportsImageAttachments(settings)
  const { transientImages: pendingImageAttachments, persistentDocuments: pendingDocumentAttachments } = splitPendingAttachments(pendingAttachments)
  const contextUsage = tokenUsage ? formatContextUsage(tokenUsage) : null
  const contextUsagePercent = tokenUsage ? getContextUsagePercent(tokenUsage) : null
  activeProjectIdRef.current = projectId
  pendingAttachmentsRef.current = pendingAttachments

  // Consume injected prompt (from S4 "继续扩写" button in StageAdvanceControl)
  useEffect(() => {
    if (injectedPrompt) {
      setInput(injectedPrompt)
      composerInputRef.current?.focus()
      onInjectedPromptConsumed?.()
    }
  }, [injectedPrompt]) // eslint-disable-line react-hooks/exhaustive-deps

  const isActiveProjectRequest = (requestProjectId) => shouldApplyProjectResponse({
    requestProject: requestProjectId,
    activeProject: activeProjectIdRef.current,
  })

  const clearAllStreamingQueues = () => {
    contentFlushTimersRef.current.forEach(timerId => clearInterval(timerId))
    contentFlushTimersRef.current.clear()
    pendingContentRef.current.clear()
  }

  useEffect(() => {
    const previousProjectId = previousProjectIdRef.current
    if (previousProjectId && previousProjectId !== projectId) {
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
      clearAllStreamingQueues()
      setLoading(false)
      setAbortController(null)
    }
    previousProjectIdRef.current = projectId

    pendingAttachments.forEach(attachment => {
      if (attachment.previewUrl) {
        URL.revokeObjectURL(attachment.previewUrl)
      }
    })
    setPendingAttachments([])
    setSelectedMaterialIds([])

    if (projectId) {
      const requestProjectId = projectId
      // 加载历史对话
      axios.get(`/api/projects/${encodeURIComponent(projectId)}/conversation`)
        .then(res => {
          if (!shouldApplyProjectResponse({
            requestProject: requestProjectId,
            activeProject: activeProjectIdRef.current,
          })) {
            return
          }
          const history = res.data.messages || []
          if (history.length > 0) {
            // 过滤掉 system/tool 消息，只显示 user/assistant
            const displayMessages = history
              .map(sanitizeAssistantMessage)
              .filter(m => m !== null)
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
          if (!shouldApplyProjectResponse({
            requestProject: requestProjectId,
            activeProject: activeProjectIdRef.current,
          })) {
            return
          }
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
      setTokenUsage(null)
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => () => {
    clearAllStreamingQueues()
    pendingAttachmentsRef.current.forEach(attachment => {
      if (attachment.previewUrl) {
        URL.revokeObjectURL(attachment.previewUrl)
      }
    })
  }, [])

  useEffect(() => {
    const textarea = composerInputRef.current
    if (!textarea) {
      return
    }

    textarea.style.height = 'auto'
    const computedStyle = window.getComputedStyle(textarea)
    const lineHeight = parseFloat(computedStyle.lineHeight || '24')
    const paddingTop = parseFloat(computedStyle.paddingTop || '0')
    const paddingBottom = parseFloat(computedStyle.paddingBottom || '0')
    const borderTop = parseFloat(computedStyle.borderTopWidth || '0')
    const borderBottom = parseFloat(computedStyle.borderBottomWidth || '0')
    const maxHeight = (lineHeight * 6) + paddingTop + paddingBottom + borderTop + borderBottom
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight)

    textarea.style.height = `${nextHeight}px`
    textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [input])

  const clearStreamingQueue = (assistantId) => {
    const timerId = contentFlushTimersRef.current.get(assistantId)
    if (timerId) {
      clearInterval(timerId)
      contentFlushTimersRef.current.delete(assistantId)
    }
    pendingContentRef.current.delete(assistantId)
  }

  const flushStreamingQueueImmediately = (assistantId, requestProjectId = activeProjectIdRef.current) => {
    if (!isActiveProjectRequest(requestProjectId)) {
      clearStreamingQueue(assistantId)
      return
    }
    const pending = pendingContentRef.current.get(assistantId) || ''
    if (pending) {
      setMessages(prev => prev.map(message =>
        message.id === assistantId ? { ...message, content: message.content + pending } : message
      ))
    }
    clearStreamingQueue(assistantId)
  }

  const enqueueAssistantContent = (assistantId, chunkText, requestProjectId) => {
    if (!isActiveProjectRequest(requestProjectId)) {
      clearStreamingQueue(assistantId)
      return
    }

    const currentPending = pendingContentRef.current.get(assistantId) || ''
    pendingContentRef.current.set(assistantId, currentPending + chunkText)

    if (contentFlushTimersRef.current.has(assistantId)) {
      return
    }

    const timerId = window.setInterval(() => {
      if (!isActiveProjectRequest(requestProjectId)) {
        clearStreamingQueue(assistantId)
        return
      }

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
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
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

  const revokeAttachmentPreview = (attachment) => {
    if (attachment?.previewUrl) {
      URL.revokeObjectURL(attachment.previewUrl)
    }
  }

  const removePendingAttachmentById = (attachmentId) => {
    setPendingAttachments(prev => {
      const target = prev.find(attachment => attachment.id === attachmentId)
      if (target) {
        revokeAttachmentPreview(target)
      }
      return removePendingAttachment(prev, attachmentId)
    })
  }

  const clearPendingAttachmentQueue = () => {
    setPendingAttachments(prev => {
      prev.forEach(revokeAttachmentPreview)
      return []
    })
  }

  const queuePendingFiles = (files) => {
    if (!files.length || !projectId || loading || uploading) {
      return
    }

    const nextPendingAttachments = files.map(file => {
      const attachment = buildPendingAttachment(file)
      if (attachment.kind === 'image') {
        return {
          ...attachment,
          previewUrl: URL.createObjectURL(file),
        }
      }
      return attachment
    })

    setPendingAttachments(prev => mergePendingAttachments(prev, nextPendingAttachments))
  }

  const uploadDocumentFiles = async (files) => {
    if (!files.length || !projectId) {
      return []
    }

    const formData = new FormData()
    files.forEach(file => formData.append('files', file))

    const res = await axios.post(
      `/api/projects/${encodeURIComponent(projectId)}/materials/upload`,
      formData,
    )
    const uploadedMaterials = res.data.materials || []
    if (uploadedMaterials.length > 0) {
      onMaterialsMerged?.(uploadedMaterials)
      onProjectMutated?.()
    }
    return uploadedMaterials
  }

  const mergeMaterialIds = (existingIds = [], newMaterials = []) => {
    const merged = [...existingIds]
    const seen = new Set(existingIds)

    for (const material of newMaterials) {
      if (!material?.id || seen.has(material.id)) {
        continue
      }
      merged.push(material.id)
      seen.add(material.id)
    }

    return merged
  }

  const buildTransientAttachmentsPayload = async (attachments = []) => {
    const payload = []

    for (const attachment of attachments) {
      payload.push({
        name: attachment.displayName,
        mime_type: attachment.mimeType,
        data_url: await fileToDataUrl(attachment.file),
      })
    }

    return payload
  }

  const sendMessage = async () => {
    const trimmedInput = input.trim()
    if (!trimmedInput || !projectId || uploading) return
    if ((selectedMaterials.some(material => material.media_kind === 'image_like') || pendingImageAttachments.length > 0) && !canSendImages) {
      showError('当前模型不支持图片输入，请切换模型或取消选择图片材料')
      return
    }

    const persistentDocumentFiles = pendingDocumentAttachments.map(attachment => attachment.file)
    let requestAttachedMaterialIds = selectedMaterialIds
    let transientAttachmentsPayload = []
    let preparationStage = 'documents'

    if (pendingDocumentAttachments.length > 0 || pendingImageAttachments.length > 0) {
      setUploading(true)
      try {
        if (persistentDocumentFiles.length > 0) {
          const uploadedMaterials = await uploadDocumentFiles(persistentDocumentFiles)
          if (uploadedMaterials.length > 0) {
            requestAttachedMaterialIds = mergeMaterialIds(selectedMaterialIds, uploadedMaterials)
            setSelectedMaterialIds(requestAttachedMaterialIds)
            setPendingAttachments(pendingImageAttachments)
            showSuccess(`已导入 ${uploadedMaterials.length} 份材料`)
          }
        }

        if (pendingImageAttachments.length > 0) {
          preparationStage = 'images'
          transientAttachmentsPayload = await buildTransientAttachmentsPayload(pendingImageAttachments)
        }
      } catch (error) {
        const detail = error?.response?.data?.detail || error?.message || '未知错误'
        const prefix = preparationStage === 'images' ? '处理图片失败: ' : '上传材料失败: '
        showError(prefix + detail)
        setUploading(false)
        return
      }
      setUploading(false)
    }

    const userMsg = {
      id: `${Date.now()}-${Math.random()}`,
      role: 'user',
      content: trimmedInput,
      attachedMaterialIds: requestAttachedMaterialIds,
    }
    setMessages(prev => [...prev, userMsg])
    const userInput = trimmedInput
    const attachedMaterialIds = requestAttachedMaterialIds
    setLoading(true)

    const controller = new AbortController()
    const requestProjectId = projectId
    abortControllerRef.current = controller
    setAbortController(controller)
    let streamFailed = false

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
          transientAttachments: transientAttachmentsPayload,
        })),
        signal: controller.signal
      })
      const responseError = await getStreamResponseError(response)
      if (responseError) {
        throw new Error(responseError)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamCompleted = false
      let readerDone = false

      while (shouldContinueSseStream({ readerDone, streamCompleted })) {
        const { done, value } = await reader.read()
        readerDone = done
        if (readerDone) break

        if (!isActiveProjectRequest(requestProjectId)) {
          clearStreamingQueue(assistantId)
          streamCompleted = true
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!isActiveProjectRequest(requestProjectId)) {
            clearStreamingQueue(assistantId)
            streamCompleted = true
            break
          }

          const data = extractSseDataPayload(line)
          if (data !== null) {
            if (data === '[DONE]') {
              flushStreamingQueueImmediately(assistantId, requestProjectId)
              streamCompleted = true
              break
            }

            try {
              const parsed = JSON.parse(data)
              if (parsed.type === 'content') {
                enqueueAssistantContent(assistantId, parsed.data, requestProjectId)
              } else if (parsed.type === 'tool') {
                if (shouldFlushStreamingQueueImmediately('tool')) {
                  flushStreamingQueueImmediately(assistantId, requestProjectId)
                }
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                // 显示工具调用信息
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: appendToolEventContent(m.content, parsed.data) } : m
                ))
              } else if (parsed.type === 'usage') {
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                setTokenUsage(parsed.data)
              } else if (parsed.type === 'system_notice') {
                // §9 system_notice: inject as a special message in the stream
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                const noticeId = `notice_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
                setMessages(prev => [
                  ...prev,
                  {
                    id: noticeId,
                    role: 'system_notice',
                    category: parsed.category || '',
                    reason: parsed.reason || '',
                    user_action: parsed.user_action || '',
                    surface_to_user: parsed.surface_to_user !== false,
                  },
                ])
              } else if (parsed.type === 'error') {
                streamFailed = true
                if (shouldFlushStreamingQueueImmediately('error')) {
                  flushStreamingQueueImmediately(assistantId, requestProjectId)
                }
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
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
      const canApplyStreamResponse = isActiveProjectRequest(requestProjectId)
      if (error.name === 'AbortError') {
        streamFailed = true
        if (canApplyStreamResponse && shouldFlushStreamingQueueImmediately('abort')) {
          flushStreamingQueueImmediately(assistantId, requestProjectId)
        }
        if (canApplyStreamResponse) {
          setMessages(prev => prev.map(m =>
            m.id === assistantId ? { ...m, content: m.content || '已停止生成' } : m
          ))
        }
      } else {
        streamFailed = true
        if (canApplyStreamResponse && shouldFlushStreamingQueueImmediately('error')) {
          flushStreamingQueueImmediately(assistantId, requestProjectId)
        }
        if (canApplyStreamResponse) {
          setMessages(prev => prev.map(m =>
            m.id === assistantId ? { ...m, content: `API调用失败: ${error.message}` } : m
          ))
        }
      }
    }
    if (abortControllerRef.current === controller) {
      abortControllerRef.current = null
    }
    if (isActiveProjectRequest(requestProjectId)) {
      setLoading(false)
      setAbortController(current => (current === controller ? null : current))
      if (!streamFailed) {
        setInput('')
        setSelectedMaterialIds([])
        clearPendingAttachmentQueue()
      } else {
        setSelectedMaterialIds(requestAttachedMaterialIds)
      }
      onProjectMutated?.()
    }
  }

  const handleSelectFiles = (event) => {
    const files = Array.from(event.target.files || [])
    queuePendingFiles(files)
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
    if (!projectId || loading || uploading) {
      return
    }
    const files = Array.from(event.dataTransfer?.files || [])
    queuePendingFiles(files)
  }

  const handleComposerPaste = (event) => {
    const clipboardItems = Array.from(event.clipboardData?.items || [])
    const files = clipboardItems
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter(Boolean)

    if (files.length === 0) {
      return
    }

    if (!projectId) {
      showInfo('请先选择或创建项目后再附加附件')
      return
    }

    queuePendingFiles(files)
  }

  const getDocumentExtension = (name = '') => {
    const segments = name.split('.')
    if (segments.length < 2) {
      return 'FILE'
    }
    return segments.pop().slice(0, 4).toUpperCase()
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
          <button onClick={onToggleWorkspacePanel} className="text-sm text-[#8888a8] hover:text-[#e2e2f0]">
            切换工作区
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => {
          // §9 system_notice — distinct warning block, yellow-orange tone
          if (msg.role === 'system_notice') {
            if (!shouldRenderSystemNoticeMessage(msg)) {
              return null
            }
            return (
              <div key={msg.id} className="flex justify-start">
                <div className="max-w-2xl w-full rounded-xl border border-[#6b4f1a] bg-[#2a1e0a] px-4 py-3 flex gap-3 items-start selectable-content">
                  <span className="text-lg leading-none mt-0.5 flex-shrink-0" aria-hidden="true">⚠️</span>
                  <div className="space-y-1 min-w-0">
                    <p className="text-sm text-[#e8b060] leading-snug">{msg.reason}</p>
                    <p className="text-xs text-[#c8904a] leading-snug">{msg.user_action}</p>
                  </div>
                </div>
              </div>
            )
          }

          const assistantBlocks = msg.role === 'assistant'
            ? splitAssistantMessageBlocks(msg.content)
            : [{ type: 'text', content: msg.content }]

          return (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-2xl px-4 py-2 rounded-lg relative group selectable-content ${
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
        <div className="border-t border-[#2a2a4a] px-4 py-3 text-xs text-[#8888a8]">
          <div className="flex flex-wrap items-center gap-2">
            <span>{contextUsage.label}</span>
            <div className="h-1.5 min-w-[160px] flex-1 rounded-full bg-[#252545] overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${contextUsagePercent == null ? 'bg-[#545d8d]/55' : 'bg-blue-500'}`}
                style={{ width: contextUsagePercent == null ? '100%' : `${contextUsagePercent}%` }}
              />
            </div>
            <span>{contextUsage.detail}</span>
            <span className="rounded-full border border-[#3a3a5a] px-2 py-0.5 text-[#c9cdf7]">
              {contextUsage.modeTag}
            </span>
            {contextUsage.compressedTag && (
              <span className="rounded-full border border-[#5a4d28] px-2 py-0.5 text-yellow-400">
                {contextUsage.compressedTag}
              </span>
            )}
          </div>
          {contextUsage.compactedStatus && (
            <div className="mt-2 text-[#9da3d9]">{contextUsage.compactedStatus}</div>
          )}
          {contextUsage.fields?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
              {contextUsage.fields.map(field => (
                <span
                  key={field.label}
                  className="rounded-full border border-[#31355e] bg-[#171a33] px-2 py-1 text-[#b8bee9]"
                >
                  {field.label}: {field.value}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className={`p-4 border-t border-[#2a2a4a] ${dragActive ? 'bg-[#20284f]' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {pendingAttachments.length > 0 && (
          <div className="mb-3">
            <div className="mb-2 text-xs uppercase tracking-[0.2em] text-[#8f93c9]">待发送附件</div>
            <div className="flex flex-wrap gap-3">
              {pendingAttachments.map(attachment => attachment.kind === 'image' ? (
                <div key={attachment.id} className="relative w-28 rounded-xl border border-[#3a3a5a] bg-[#12142a] p-2">
                  <button
                    type="button"
                    onClick={() => removePendingAttachmentById(attachment.id)}
                    className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-[#0d0f20] text-[11px] text-[#e2e2f0] hover:bg-[#232852]"
                    title="移除附件"
                  >
                    ×
                  </button>
                  <div className="mb-2 h-16 overflow-hidden rounded-lg bg-[#0f1226]">
                    {attachment.previewUrl ? (
                      <img
                        src={attachment.previewUrl}
                        alt={attachment.displayName}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-xs text-[#8f93c9]">IMAGE</div>
                    )}
                  </div>
                  <div className="truncate text-xs text-[#e2e2f0]">{attachment.displayName}</div>
                  <div className="mt-1 inline-flex rounded-full bg-[#253464] px-2 py-0.5 text-[10px] text-[#dce5ff]">
                    本轮临时
                  </div>
                </div>
              ) : (
                <div key={attachment.id} className="relative flex min-w-[220px] items-center gap-3 rounded-xl border border-[#3a3a5a] bg-[#12142a] px-3 py-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#243057] text-[11px] font-semibold text-[#dce5ff]">
                    {getDocumentExtension(attachment.displayName)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-[#e2e2f0]">{attachment.displayName}</div>
                    <div className="mt-1 inline-flex rounded-full bg-[#1f3c2f] px-2 py-0.5 text-[10px] text-[#dff7e7]">
                      发送前入库
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removePendingAttachmentById(attachment.id)}
                    className="flex h-6 w-6 items-center justify-center rounded-full bg-[#0d0f20] text-xs text-[#e2e2f0] hover:bg-[#232852]"
                    title="移除附件"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
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
        {!canSendImages && (selectedMaterials.some(material => material.media_kind === 'image_like') || pendingImageAttachments.length > 0) && (
          <div className="mb-3 text-xs text-[#f5b16a]">
            当前自定义模型按保守规则视为不支持图片输入，选中图片材料或待发送图片时会阻止发送。
          </div>
        )}
        {dragActive && (
          <div className="mb-3 rounded border border-dashed border-[#6d8cff] px-3 py-2 text-sm text-[#d9e2ff]">
            松开鼠标即可加入待发送附件
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
            title="添加待发送附件"
          >
            {uploading ? '处理中...' : '+'}
          </button>
          <textarea
            ref={composerInputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onPaste={handleComposerPaste}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={() => setIsComposing(false)}
            onKeyDown={e => {
              if (shouldSubmitComposerKeydown({
                key: e.key,
                shiftKey: e.shiftKey,
                isComposing: e.nativeEvent?.isComposing || isComposing,
              })) {
                e.preventDefault()
                sendMessage()
              }
            }}
            rows={1}
            placeholder="输入消息...（Enter 发送，Shift+Enter 换行）"
            disabled={loading || uploading}
            className="flex-1 resize-none bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded-lg px-4 py-2 focus:outline-none focus:border-blue-500"
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
