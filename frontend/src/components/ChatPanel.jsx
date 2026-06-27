import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { showError, showInfo, showSuccess } from '../utils/toast'
import { buildChatRequest, buildTransientAttachmentsPayload, conversionStatusChip, toggleMaterialSelection } from '../utils/chatMaterials'
import { applyAttachmentTranscribed, historyTranscriptIndicators } from '../utils/sseEvents'
import { closePendingToolEvents, reduceToolEvent } from '../utils/toolEvents'
// 时间线穿插：每个写 msg.content 的 SSE handler 旁建 msg.parts（文本/工具按到达顺序交错）。
import {
  appendErrorPart,
  applyToolEventToParts,
  closePendingToolParts,
  mutateCurrentTextPart,
  partsToText,
} from '../utils/messageParts'
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
import ToolCallList from './ToolCallList'
// Shared markdown rendering fragment, reused by the S5 ReviewChatWindow (same look & feel).
import { assistantMarkdownComponents } from './MarkdownMessage'
import { IconTrash, IconSidebar, IconPanelRight, IconPaperclip, IconSend, IconStop, IconClose } from './icons'

const ChatPanel = forwardRef(function ChatPanel({
  projectId,
  project,
  settings,
  workspace,
  materials,
  onMaterialsMerged,
  onProjectMutated,
  onToggleSidebar,
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
  // C5: queued system triggers (independent-review completions that arrived while the chat was busy).
  const pendingTriggerQueueRef = useRef([])
  // 每次普通发送自增的序号；失败恢复时校验「仍是最近一次发送」，防旧的被中止发送把原文盖回
  // 已被新发送清空的输入框（codex 红队 abort race v2）。
  const sendSeqRef = useRef(0)
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
                // 时间线穿插 reload：后端 /conversation 给 assistant 返回有序 parts（文本/工具交错）；
                // 老消息无字段→undefined（IP6 渲染回退到 content/toolEvents，不回归）。
                parts: m.parts,
                // Tool-pill reload: 后端 /conversation 给每条 assistant 返回结构化 tool_events
                // 并列字段（老消息无字段→[]），直接进 msg.toolEvents 由 ToolCallList 渲染。
                toolEvents: m.tool_events || [],
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
        // 旁建 parts：本次冲刷出去的同一段 `pending` 并入当前 text 片段（content 写入不变）。
        message.id === assistantId ? { ...message, content: message.content + pending, parts: mutateCurrentTextPart(message.parts || [], t => t + pending) } : message
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
        // 旁建 parts：本次切片冲出的同一段 `emitted` 并入当前 text 片段（content 写入不变）。
        message.id === assistantId ? { ...message, content: message.content + emitted, parts: mutateCurrentTextPart(message.parts || [], t => t + emitted) } : message
      ))

      if (!remaining) {
        clearStreamingQueue(assistantId)
      }
    }, 24)

    contentFlushTimersRef.current.set(assistantId, timerId)
  }

  const clearConversation = async () => {
    if (loading || uploading) return  // 生成/上传中禁清空：避免与持锁的聊天轮竞争（后端端点已离 loop，前端再加一道）
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

  const copyMessage = (message) => {
    // parts 非空时复制源用有序片段拼回的文本，否则回退 content；两者都仍过 strip（含 thinking/tool-log 标记）。
    const cleanText = getCopyableAssistantMessageText(message?.parts?.length ? partsToText(message.parts) : (message?.content || ''))
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
        credentials: 'include',
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
                  m.id === assistantId ? { ...m, content: appendThinkingEventContent(m.content, parsed.data), parts: mutateCurrentTextPart(m.parts || [], t => appendThinkingEventContent(t, parsed.data)) } : m
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
                  m.id === assistantId ? { ...m, content: appendToolEventContent(m.content, parsed.data), parts: mutateCurrentTextPart(m.parts || [], t => appendToolEventContent(t, parsed.data)) } : m
                ))
              } else if (parsed.type === 'tool_call' || parsed.type === 'tool_result') {
                // 结构化工具事件（tool-pill）：tool_call 到来时先冲刷已排队的流式文本，
                // 让 pill 成组渲染在正文之上（与诊断 type:"tool" 文本分流，互不影响）。
                if (parsed.type === 'tool_call' && shouldFlushStreamingQueueImmediately('tool')) {
                  flushStreamingQueueImmediately(assistantId, requestProjectId)
                }
                if (!isActiveProjectRequest(requestProjectId)) {
                  streamCompleted = true
                  break
                }
                setMessages(prev => prev.map(m =>
                  // 旁建 parts：同一事件经 applyToolEventToParts 插入/更新有序片段（与 toolEvents 并行、互不影响）。
                  m.id === assistantId ? { ...m, toolEvents: reduceToolEvent(m.toolEvents || [], parsed), parts: applyToolEventToParts(m.parts || [], parsed) } : m
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
                // 防御性：万一未来某后端路径在 tool_call 后发裸 error 不先 flush 收尾 tool_result，
                // 前端自洽地收尾仍 pending 的工具 pill（当前后端先 flush，故多数情况是 no-op）。
                setMessages(prev => prev.map(m =>
                  m.id === assistantId
                    ? { ...m, content: `错误: ${parsed.data}`, toolEvents: closePendingToolEvents(m.toolEvents, '生成出错'), parts: closePendingToolParts(appendErrorPart(m.parts || [], `错误: ${parsed.data}`), '生成出错') }
                    : m
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
          // 中止：收尾任何仍 pending 的工具 pill（后端 GeneratorExit 上不发收尾 tool_result，
          // 否则会留永久转圈 pill），其它字段不动。
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              // parts 同样仅当无可见文本时才追加「已停止生成」（有文本则 displayText='' → appendErrorPart no-op）。
              ? { ...m, content: m.content || '已停止生成', toolEvents: closePendingToolEvents(m.toolEvents, '已停止生成'), parts: closePendingToolParts(appendErrorPart(m.parts || [], (partsToText(m.parts) || m.content) ? '' : '已停止生成'), '已停止生成') }
              : m
          ))
        }
      } else {
        streamFailed = true
        if (canApplyStreamResponse && shouldFlushStreamingQueueImmediately('error')) {
          flushStreamingQueueImmediately(assistantId, requestProjectId)
        }
        if (canApplyStreamResponse) {
          // 网络 / fetch 失败：同样收尾仍 pending 的工具 pill（断连后端不发收尾 tool_result）。
          setMessages(prev => prev.map(m =>
            m.id === assistantId
              ? { ...m, content: `API调用失败: ${error.message}`, toolEvents: closePendingToolEvents(m.toolEvents, '连接中断'), parts: closePendingToolParts(appendErrorPart(m.parts || [], `API调用失败: ${error.message}`), '连接中断') }
              : m
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
        // 输入框清空已由 sendMessage 乐观完成；此处只清材料选择 / 附件队列。
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
  // scoped to this project) so a finished independent review is never silently dropped; otherwise fire
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

    // chatbox 风格乐观清空：点发送即把消息转移到气泡、输入框立刻清空（不再等这一轮回答结束）。
    // 任一失败路径（上传失败 / 发送失败 / 中止）再把原文恢复回输入框，保留可重试体验。
    // 恢复经 restoreInputForRetry 双重守卫：① 序号未变（其间未发起更新的发送）② 输入框仍为空
    //（用户未另起新输入）。两者缺一都不回填——既防 abort 后覆盖用户新打的字，也防旧的被中止
    // 发送把原文盖回已被「下一条发送」清空的输入框。
    const sendSeq = ++sendSeqRef.current
    const restoreInputForRetry = () => {
      if (sendSeqRef.current !== sendSeq) return
      setInput(prev => prev === '' ? trimmedInput : prev)
    }
    setInput('')

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
        restoreInputForRetry() // 上传失败：守卫式回填原文便于重试
        return
      }
      setUploading(false)
    }

    const streamOk = await startStream({
      messageText: trimmedInput,
      systemTrigger: null,
      attachedMaterialIds: requestAttachedMaterialIds,
      transientAttachments: transientAttachmentsPayload,
      renderUserBubble: true,
    })
    if (!streamOk) {
      restoreInputForRetry() // 发送失败 / 用户中止：守卫式回填原文便于重试
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
    <div className="flex-1 min-w-0 bg-chat flex flex-col">
      <div className="h-[60px] flex-shrink-0 border-b border-track px-[22px] flex items-center justify-between gap-2">
        <div className="flex items-center gap-[10px] min-w-0">
          {onToggleSidebar && (
            <button
              onClick={onToggleSidebar}
              title="切换侧栏"
              aria-label="切换侧栏"
              className="flex items-center justify-center w-8 h-8 flex-shrink-0 border border-border bg-card2 rounded-ibtn text-t2 hover:text-text"
            >
              <IconSidebar size={15} />
            </button>
          )}
          <div className="min-w-0">
            <h2 className="text-base font-bold text-text tracking-tight truncate">{project?.name || '请选择或创建项目'}</h2>
            {projectId && (
              <p className="text-xs text-t3 mt-[1px] truncate">
                {connection.title} · 当前阶段 {workspaceSummary.stageLabel}
              </p>
            )}
          </div>
        </div>
        <div className="flex gap-[6px] flex-shrink-0">
          {projectId && (
            <button
              onClick={clearConversation}
              disabled={loading || uploading}
              title="清空对话"
              className="flex items-center justify-center w-8 h-8 border border-border bg-card2 rounded-ibtn text-t2 hover:text-text disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-t2"
            >
              <IconTrash size={15} />
            </button>
          )}
          <button
            onClick={onToggleWorkspacePanel}
            title="切换工作区"
            className="flex items-center justify-center w-8 h-8 border border-border bg-card2 rounded-ibtn text-accent hover:bg-card2/70"
          >
            <IconPanelRight size={15} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-[22px] flex flex-col gap-[18px]">
        {messages.map((msg) => {
          // §9 system_notice — distinct warning block, warn tone
          if (msg.role === 'system_notice') {
            if (!shouldRenderSystemNoticeMessage(msg)) {
              return null
            }
            return (
              <div key={msg.id} className="flex gap-[10px] items-start border border-warn/30 bg-warn/10 rounded-[9px] px-[13px] py-[10px] selectable-content">
                <svg
                  width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  className="text-warn flex-shrink-0 mt-[1px]" aria-hidden="true"
                >
                  <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                <div className="space-y-1 min-w-0">
                  <p className="text-13 text-warn leading-snug">{msg.reason}</p>
                  <p className="text-xs text-warn/80 leading-snug">{msg.user_action}</p>
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

          // 附件 / 转写指示行（用户气泡内复用，token 配色）
          const attachmentIndicators = (
            <>
              {msg.attachedMaterialIds?.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2">
                  {msg.attachedMaterialIds.map(materialId => {
                    const attachedMaterial = materials.find(material => material.id === materialId)
                    return (
                      <span key={materialId} className="text-11 px-2 py-1 rounded-tag bg-white/10 border border-white/20 text-white/85">
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
                      className={`text-11 px-2 py-1 rounded-tag border ${
                        attachment.transcriptionStatus === 'failed'
                          ? 'bg-error/20 border-error/40 text-white'
                          : 'bg-white/10 border-white/20 text-white/85'
                      }`}
                    >
                      {attachment.transcribed
                        ? '已转写图片'
                        : attachment.transcriptionStatus === 'failed'
                        ? `图片没读出来：${attachment.name || '图片'}`
                        : `${attachment.name || '图片'}`}
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
                      className={`text-11 px-2 py-1 rounded-tag border ${
                        indicator.status === 'failed'
                          ? 'bg-error/20 border-error/40 text-white'
                          : 'bg-white/10 border-white/20 text-white/85'
                      }`}
                    >
                      {indicator.status === 'parsed'
                        ? '已转写图片'
                        : `图片没读出来：${indicator.name || '图片'}`}
                    </span>
                  ))}
                </div>
              )}
            </>
          )

          if (msg.role === 'assistant') {
            // 助手消息：圆点 + 助手标签 + 无底色正文（非气泡）
            return (
              <div key={msg.id} className="relative group selectable-content">
                <div className="flex items-center gap-[7px] mb-[9px]">
                  <span className="w-[6px] h-[6px] rounded-full bg-abright flex-shrink-0" aria-hidden="true" />
                  <span className="text-13 font-semibold text-text">助手</span>
                </div>
                <div className="space-y-2 text-15 leading-[1.68] text-text">
                  {/* 工具调用 pill 成组渲染在正文上方（live SSE reduceToolEvent / reload tool_events）。 */}
                  <ToolCallList toolEvents={msg.toolEvents} />
                  {assistantBlocks.map((block, index) => block.type === 'thinking' ? (
                    <ThinkingBlock key={index} text={block.content} />
                  ) : (
                    <ReactMarkdown
                      key={index}
                      className="max-w-none"
                      remarkPlugins={[remarkGfm]}
                      components={assistantMarkdownComponents}
                    >
                      {block.content}
                    </ReactMarkdown>
                  ))}
                </div>
                <button
                  onClick={() => copyMessage(msg)}
                  className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 text-11 px-2 py-1 bg-card2 border border-border rounded-tag text-t2 hover:text-text transition-opacity"
                  title="复制"
                >
                  复制
                </button>
              </div>
            )
          }

          // 用户消息：右对齐气泡
          return (
            <div key={msg.id} className="flex justify-end">
              <div className="relative group self-end max-w-[500px] bg-userbub text-white rounded-[13px_13px_4px_13px] px-[14px] py-[10px] text-15 leading-[1.55] selectable-content">
                {attachmentIndicators}
                <div className="whitespace-pre-wrap">{msg.content}</div>
                <button
                  onClick={() => copyMessage(msg)}
                  className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 text-11 px-2 py-1 bg-white/15 rounded-tag text-white hover:bg-white/25 transition-opacity"
                  title="复制"
                >
                  复制
                </button>
              </div>
            </div>
          )
        })}
        {loading && (
          <div className="flex items-center gap-2 text-t3 text-13">
            <span className="w-[6px] h-[6px] rounded-full bg-abright animate-pulse flex-shrink-0" aria-hidden="true" />
            正在思考...
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {tokenUsage && (
        <div className="flex-shrink-0 border-t border-track px-6 py-2 text-11 text-t3">
          <div className="flex flex-wrap items-center gap-[10px]">
            <span className="flex-shrink-0">{contextUsage.label}</span>
            {/* 进度条 flex-1 随聊天窗口伸缩；去掉 max-w 和 modeTag 的 ml-auto，让条变长、
                把「用量/上限」数字推到贴近右侧 Provider 标签处（用户反馈：条太短、数字该更靠右）。 */}
            <div className="h-1 flex-1 min-w-[140px] rounded-full bg-track overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${contextUsagePercent == null ? 'bg-t3/55' : 'bg-abright'}`}
                style={{ width: contextUsagePercent == null ? '100%' : `${contextUsagePercent}%` }}
              />
            </div>
            <span className="text-text font-mono flex-shrink-0">{contextUsage.detail}</span>
            <span className="text-2xs text-asoftt bg-asoft border border-asoftb px-2 py-px rounded-chip flex-shrink-0">
              {contextUsage.modeTag}
            </span>
            {contextUsage.compressedTag && (
              <span className="text-2xs text-warn border border-warn/40 px-2 py-px rounded-chip">
                {contextUsage.compressedTag}
              </span>
            )}
          </div>
          {contextUsage.compactedStatus && (
            <div className="mt-2 text-t3">{contextUsage.compactedStatus}</div>
          )}
          {contextUsage.fields?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2 text-11">
              {contextUsage.fields.map(field => (
                <span
                  key={field.label}
                  className="rounded-chip border border-border bg-card2 px-2 py-1 text-t2"
                >
                  {field.label}: {field.value}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className="flex-shrink-0 px-6 pt-3 pb-[18px]"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {dragActive && (
          <div className="mb-[9px] border border-dashed border-abright bg-asoft rounded-btn px-3 py-2 text-12 text-asoftt">
            松开鼠标即可加入待发送附件
          </div>
        )}
        {pendingAttachments.length > 0 && (
          <div className="mb-3">
            <div className="mb-2 text-11 uppercase tracking-[0.2em] text-t3">待发送附件</div>
            <div className="flex flex-wrap gap-3">
              {pendingAttachments.map(attachment => attachment.kind === 'image' ? (
                <div key={attachment.id} className="relative w-28 rounded-card border border-border bg-card2 p-2">
                  <button
                    type="button"
                    onClick={() => removePendingAttachmentById(attachment.id)}
                    className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-card2 border border-border text-t2 hover:text-text"
                    title="移除附件"
                  >
                    <IconClose size={11} />
                  </button>
                  <div className="mb-2 h-16 overflow-hidden rounded-btn bg-field">
                    {attachment.previewUrl ? (
                      <img
                        src={attachment.previewUrl}
                        alt={attachment.displayName}
                        className="h-full w-full object-cover"
                      />
                    ) : (
                      <div className="flex h-full items-center justify-center text-12 text-t3">IMAGE</div>
                    )}
                  </div>
                  <div className="truncate text-12 text-text">{attachment.displayName}</div>
                  <div className="mt-1 inline-flex rounded-chip bg-asoft border border-asoftb px-2 py-0.5 text-2xs text-asoftt">
                    本轮临时
                  </div>
                </div>
              ) : (
                <div key={attachment.id} className="relative flex min-w-[220px] items-center gap-3 rounded-card border border-border bg-card2 px-3 py-2">
                  <div className="flex h-10 w-10 items-center justify-center rounded-btn bg-asoft text-11 font-semibold text-asoftt">
                    {getDocumentExtension(attachment.displayName)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-13 text-text">{attachment.displayName}</div>
                    <div className="mt-1 inline-flex rounded-chip bg-success/15 border border-success/30 px-2 py-0.5 text-2xs text-success">
                      发送前入库
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removePendingAttachmentById(attachment.id)}
                    className="flex h-6 w-6 items-center justify-center rounded-full bg-card2 border border-border text-t2 hover:text-text"
                    title="移除附件"
                  >
                    <IconClose size={12} />
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
                className="text-12 px-2 py-1 rounded-tag bg-asoft border border-asoftb text-asoftt inline-flex items-center gap-1 hover:bg-asoft/70"
              >
                <span>{material.display_name}</span>
                <IconClose size={11} />
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
                  className={`text-12 px-2 py-1 rounded-tag border inline-flex items-center gap-1 ${
                    selectedMaterialIds.includes(material.id)
                      ? 'bg-accent border-accent text-white'
                      : 'bg-card2 border-border text-t2 hover:text-text'
                  }`}
                >
                  <span>{material.display_name}</span>
                  {statusChip && (
                    <span
                      title={statusChip.title || undefined}
                      className={`text-2xs px-1.5 py-0.5 rounded-chip ${
                        statusChip.tone === 'failed'
                          ? 'bg-error/20 text-error'
                          : statusChip.tone === 'not_parsed'
                          ? 'bg-track text-t3'
                          : 'bg-success/15 text-success'
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
        <div className="flex items-end gap-[9px] border border-track rounded-card bg-field px-[7px] py-[7px] pl-[10px]">
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
            className="flex items-center justify-center w-8 h-8 flex-shrink-0 rounded-btn border border-border bg-card2 text-t2 hover:text-text disabled:opacity-40 disabled:cursor-not-allowed"
            title="添加待发送附件"
          >
            {uploading ? <span className="text-2xs">处理中</span> : <IconPaperclip size={16} />}
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
            className="flex-1 resize-none border-none outline-none bg-transparent text-15 text-text py-[7px] max-h-[120px] placeholder:text-t3"
          />
          {loading ? (
            <button
              onClick={stopGeneration}
              className="flex items-center gap-[6px] h-8 px-4 flex-shrink-0 rounded-btn border border-error/50 text-error text-13 font-medium hover:bg-error/10"
            >
              <IconStop size={13} />
              停止
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!projectId || uploading}
              className="flex items-center gap-[6px] h-8 px-4 flex-shrink-0 rounded-btn bg-accent text-white text-13 font-medium hover:bg-accent/90 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              发送
              <IconSend size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
})

export default ChatPanel
