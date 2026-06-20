import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { showError, showInfo, showSuccess } from '../utils/toast'
import { buildChatRequest, buildTransientAttachmentsPayload, conversionStatusChip, toggleMaterialSelection } from '../utils/chatMaterials'
import { applyAttachmentTranscribed, historyTranscriptIndicators } from '../utils/sseEvents'
import {
  createPendingTriggerItem,
  dequeuePendingTrigger,
  dropPendingTriggersByType,
  enqueuePendingTrigger,
  scopePendingQueueToProject,
} from '../utils/pendingTriggerQueue'
import {
  appendThinkingEventContent,
  appendToolEventContent,
  buildProjectWelcomeMessage,
  extractSseDataPayload,
  getCopyableAssistantMessageText,
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
import {
  buildPendingAttachment,
  fileToDataUrl,
  mergePendingAttachments,
  removePendingAttachment,
  splitPendingAttachments,
} from '../utils/pendingAttachments'
import { shouldApplyProjectResponse } from '../utils/projectRequestOwnership'
import { stripToolLogComments } from '../utils/toolLogStrip.mjs'
import { summarizeWorkspace } from '../utils/workspaceSummary'
import ThinkingBlock from './ThinkingBlock'
// Shared markdown rendering fragment, reused by the S5 ReviewChatWindow (same look & feel).
import { assistantMarkdownComponents } from './MarkdownMessage'

const ChatPanel = forwardRef(function ChatPanel({
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
}, ref) {
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
  // C5: queued system triggers (review/lint completions that arrived while the chat was busy).
  const pendingTriggerQueueRef = useRef([])
  const connection = describeConnectionMode(settings || {})
  const workspaceSummary = summarizeWorkspace(workspace || {})
  const selectedMaterials = materials.filter(material => selectedMaterialIds.includes(material.id))
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
      // Drop pending triggers that belong to the project we just left — re-issuing them under
      // the new project would be run-bound-rejected by the backend and surface a spurious error
      // while the old project's review stays unreported.
      pendingTriggerQueueRef.current = scopePendingQueueToProject(pendingTriggerQueueRef.current, projectId)
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
                // N6 Fix2: carry persisted image transcripts so a reloaded chat re-shows the
                // 已转写图片 / 图片没读出来 indicator (live-in-turn transientAttachments are gone).
                historyTranscripts: historyTranscriptIndicators(m),
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
    const cleanText = getCopyableAssistantMessageText(content || '')
    navigator.clipboard.writeText(cleanText).then(() => {
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

  const buildTransientAttachments = async (attachments = []) => {
    const resolved = []

    for (const attachment of attachments) {
      // Preserve the pending-attachment id so attachment_transcribed SSE events correlate back
      // to the exact bubble attachment (backend echoes this as attachment_id).
      resolved.push({
        id: attachment.id,
        name: attachment.displayName,
        mime_type: attachment.mimeType,
        data_url: await fileToDataUrl(attachment.file),
      })
    }

    return buildTransientAttachmentsPayload(resolved)
  }

  const startStream = useCallback(async ({
    messageText = '',
    systemTrigger = null,
    triggerMetadata = null,
    attachedMaterialIds = [],
    transientAttachments = [],
    renderUserBubble = true,
  }) => {
    if (!projectId || loading || uploading) return false

    const requestProjectId = projectId
    const requestMessageText = systemTrigger
      ? (typeof messageText === 'string' ? messageText : '')
      : (typeof messageText === 'string' ? messageText.trim() : '')
    if (!requestMessageText && !systemTrigger) return false

    // client_message_id correlates the user bubble with attachment_transcribed SSE events.
    // Only a normal user turn that renders a bubble needs/sends one (system triggers omit it).
    const clientMessageId = (renderUserBubble && !systemTrigger)
      ? `${Date.now()}-${Math.random()}`
      : null

    if (renderUserBubble) {
      const userMsg = {
        id: clientMessageId || `${Date.now()}-${Math.random()}`,
        role: 'user',
        content: requestMessageText,
        attachedMaterialIds,
        // Keep the transient image attachments (with their ids) on the bubble so an incoming
        // attachment_transcribed event can mark the exact attachment as transcribed / failed.
        transientAttachments,
      }
      setMessages(prev => [...prev, userMsg])
    }

    setLoading(true)

    const controller = new AbortController()
    abortControllerRef.current = controller
    setAbortController(controller)
    let streamFailed = false

    const assistantId = `${Date.now()}-${Math.random()}`
    clearStreamingQueue(assistantId)
    setMessages(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }])

    try {
      const response = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildChatRequest({
          projectId: requestProjectId,
          messageText: requestMessageText,
          attachedMaterialIds,
          transientAttachments,
          systemTrigger,
          triggerMetadata,
          clientMessageId,
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
              } else if (parsed.type === 'thinking') {
                flushStreamingQueueImmediately(assistantId, requestProjectId)
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                setMessages(prev => prev.map(m =>
                  m.id === assistantId ? { ...m, content: appendThinkingEventContent(m.content, parsed.data) } : m
                ))
              } else if (parsed.type === 'tool') {
                if (shouldFlushStreamingQueueImmediately('tool')) {
                  flushStreamingQueueImmediately(assistantId, requestProjectId)
                }
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
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
              } else if (parsed.type === 'attachment_transcribed') {
                // N6 D2: mark the matched transient image on its user bubble as transcribed /
                // failed (pure update; no-op when no bubble/attachment matches).
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                setMessages(prev => applyAttachmentTranscribed(prev, parsed.data))
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
      if (!streamFailed && renderUserBubble) {
        setInput('')
        setSelectedMaterialIds([])
        clearPendingAttachmentQueue()
      } else if (streamFailed && renderUserBubble) {
        setSelectedMaterialIds(attachedMaterialIds)
      }
      onProjectMutated?.()
      // C5: the chat is free again — flush the next queued system trigger (if any) for the
      // active project, re-issuing it with its ORIGINAL run-bound metadata. setTimeout lets the
      // setLoading(false) above settle so the flushed startStream isn't blocked by stale loading.
      setTimeout(() => flushNextPendingTriggerRef.current?.(), 0)
    }
    return !streamFailed
  }, [
    projectId,
    loading,
    uploading,
    clearStreamingQueue,
    flushStreamingQueueImmediately,
    isActiveProjectRequest,
    enqueueAssistantContent,
    clearPendingAttachmentQueue,
    onProjectMutated,
  ])

  // Keep a ref to the freshest startStream so deferred flushes always use a non-stale closure
  // (post-stream loading=false), avoiding the loading-guard early-return.
  const startStreamRef = useRef(startStream)
  startStreamRef.current = startStream

  // triggerSystemTurn(triggerType, metadata): if the chat is busy, queue the trigger (FIFO,
  // scoped to this project) so a finished review/lint is never silently dropped; otherwise fire
  // it now. metadata = { run_id, report_mtime_ns } (opaque strings, threaded verbatim).
  const triggerSystemTurn = useCallback((triggerType, metadata = null) => {
    if (loading || uploading) {
      pendingTriggerQueueRef.current = enqueuePendingTrigger(
        pendingTriggerQueueRef.current,
        createPendingTriggerItem({
          triggerType,
          runId: metadata?.run_id ?? null,
          reportMtimeNs: metadata?.report_mtime_ns ?? null,
          projectId,
        }),
      )
      return
    }
    startStream({
      messageText: '',
      systemTrigger: triggerType,
      triggerMetadata: metadata,
      renderUserBubble: false,
    })
  }, [startStream, loading, uploading, projectId])

  const flushNextPendingTrigger = useCallback(() => {
    const { item, queue } = dequeuePendingTrigger(pendingTriggerQueueRef.current, activeProjectIdRef.current)
    pendingTriggerQueueRef.current = queue
    if (!item) return
    startStreamRef.current?.({
      messageText: '',
      systemTrigger: item.triggerType,
      triggerMetadata: { run_id: item.run_id, report_mtime_ns: item.report_mtime_ns },
      renderUserBubble: false,
    })
  }, [])

  const flushNextPendingTriggerRef = useRef(flushNextPendingTrigger)
  flushNextPendingTriggerRef.current = flushNextPendingTrigger

  // Drop pending same-type triggers for the active project when the user STARTS a new run (called
  // from WorkspacePanel). The new run overwrites the store tombstone, so a stale pending flush
  // would be run-bound-rejected and report the older successful review as a spurious error (B2).
  const dropPendingReviewTriggers = useCallback((triggerType) => {
    pendingTriggerQueueRef.current = dropPendingTriggersByType(
      pendingTriggerQueueRef.current,
      triggerType,
      activeProjectIdRef.current,
    )
  }, [])

  useImperativeHandle(ref, () => ({ triggerSystemTurn, dropPendingReviewTriggers }), [triggerSystemTurn, dropPendingReviewTriggers])

  const sendMessage = async () => {
    const trimmedInput = input.trim()
    if (!trimmedInput || !projectId || uploading) return

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
          transientAttachmentsPayload = await buildTransientAttachments(pendingImageAttachments)
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

    await startStream({
      messageText: trimmedInput,
      systemTrigger: null,
      attachedMaterialIds: requestAttachedMaterialIds,
      transientAttachments: transientAttachmentsPayload,
      renderUserBubble: true,
    })
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

          const cleanContent = msg.role === 'assistant'
            ? stripToolLogComments(msg.content || '')
            : msg.content
          const assistantBlocks = msg.role === 'assistant'
            ? splitAssistantMessageBlocks(cleanContent)
            : [{ type: 'text', content: cleanContent }]

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
                {msg.transientAttachments?.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {msg.transientAttachments.map(attachment => (
                      <span
                        key={attachment.id || attachment.name}
                        className={`text-[11px] px-2 py-1 rounded-full border ${
                          attachment.transcriptionStatus === 'failed'
                            ? 'bg-[#3a1a1a] border-[#6b3a3a] text-[#f0b8b8]'
                            : 'bg-[#1a1a2e] border-[#3a3a5a] text-[#b8bbe8]'
                        }`}
                      >
                        {attachment.transcribed
                          ? '📎 已转写图片'
                          : attachment.transcriptionStatus === 'failed'
                          ? `⚠️ 图片没读出来：${attachment.name || '图片'}`
                          : `🖼️ ${attachment.name || '图片'}`}
                      </span>
                    ))}
                  </div>
                )}
                {/* N6 Fix2: reloaded-history transcripts (no live transientAttachments) — same
                    indicator branch as above so a refreshed chat keeps the 已转写图片 / 没读出来 note. */}
                {msg.historyTranscripts?.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {msg.historyTranscripts.map(indicator => (
                      <span
                        key={indicator.id}
                        className={`text-[11px] px-2 py-1 rounded-full border ${
                          indicator.status === 'failed'
                            ? 'bg-[#3a1a1a] border-[#6b3a3a] text-[#f0b8b8]'
                            : 'bg-[#1a1a2e] border-[#3a3a5a] text-[#b8bbe8]'
                        }`}
                      >
                        {indicator.status === 'parsed'
                          ? '📎 已转写图片'
                          : `⚠️ 图片没读出来：${indicator.name || '图片'}`}
                      </span>
                    ))}
                  </div>
                )}
                {msg.role === 'assistant' ? (
                  <div className="space-y-2">
                    {assistantBlocks.map((block, index) => block.type === 'tool' ? (
                      <div key={index} className="text-xs bg-[#1a1a2e] px-2 py-1 rounded border border-[#3a3a5a] text-[#8888a8] font-mono">
                        {block.content}
                      </div>
                    ) : block.type === 'thinking' ? (
                      <ThinkingBlock key={index} text={block.content} />
                    ) : (
                      <ReactMarkdown
                        key={index}
                        className="prose prose-invert prose-sm max-w-none"
                        remarkPlugins={[remarkGfm]}
                        components={assistantMarkdownComponents}
                      >
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
            {materials.map(material => {
              const statusChip = conversionStatusChip(material)
              return (
                <button
                  key={material.id}
                  type="button"
                  onClick={() => setSelectedMaterialIds(prev => toggleMaterialSelection(prev, material.id))}
                  className={`text-xs px-2 py-1 rounded-full border inline-flex items-center gap-1 ${
                    selectedMaterialIds.includes(material.id)
                      ? 'bg-blue-600 border-blue-500 text-white'
                      : 'bg-[#15162d] border-[#2f3158] text-[#b6b8de]'
                  }`}
                >
                  <span>{material.display_name}</span>
                  {statusChip && (
                    <span
                      title={statusChip.title || undefined}
                      className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                        statusChip.tone === 'failed'
                          ? 'bg-[#3a1a1a] text-[#f0b8b8]'
                          : statusChip.tone === 'not_parsed'
                          ? 'bg-[#1e1f3a] text-[#8e92bd]'
                          : 'bg-[#15402a] text-[#9fe0bd]'
                      }`}
                    >
                      {statusChip.label}
                    </span>
                  )}
                </button>
              )
            })}
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
})

export default ChatPanel
