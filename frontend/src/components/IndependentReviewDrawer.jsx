import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  aggregateContentDelta,
  backoffExhausted,
  genRunId,
  initialReviewWindowState,
  nextBackoff,
  parseDrawerEvent,
  reviewWindowReducer,
} from '../utils/independentReviewDrawer'
import { normalizeApiErrorDetail } from '../utils/authError'
import { MarkdownMessage } from './MarkdownMessage'
import ToolCallPill from './ToolCallPill'

// S5 ReviewChatWindow: a draggable, closeable mini-chat that streams the independent-review
// agent's reasoning. It generates a stable run_id on open (constant for the window's lifetime),
// drives the review over POST /independent-review/stream with resume support, and on completion
// hands {run_id, report_mtime_ns} back so the main agent can run-bind the report.
export default function ReviewChatWindow({
  projectId,
  isOpen,
  onClose,
  onCompleted,
  isMobile = false,
}) {
  const [bubbles, setBubbles] = useState([])
  const [windowState, setWindowState] = useState(initialReviewWindowState())
  const [position, setPosition] = useState({ x: null, y: null })
  // Optional supplement the user types in the errored panel before resuming (plan Task 5.2:
  // "解锁输入框，用户在断掉处输入，让子代理带累计上下文从断处继续审"). Empty unless errored.
  const [supplementInput, setSupplementInput] = useState('')

  const runIdRef = useRef(null)
  const abortControllerRef = useRef(null)
  const backoffTimerRef = useRef(null)
  const dragRef = useRef(null)
  const completedRef = useRef(false)
  const closingRef = useRef(false)
  // Tracks the isOpen rising edge: a review starts only when the window actually OPENS, never when
  // projectId changes while it is already open. WorkspacePanel closes the window on project switch
  // via an async effect, so for one render the window can see the NEW projectId with isOpen still
  // true — without this guard the open-effect would fire a fresh review against the wrong project
  // (wasting a run + orphaning a session in the new project).
  const openedRef = useRef(false)
  const onCompletedRef = useRef(onCompleted)
  onCompletedRef.current = onCompleted
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  const applyEvent = useCallback((event) => {
    setBubbles(prev => aggregateContentDelta(prev, event))
    setWindowState(prev => reviewWindowReducer(prev, event))
  }, [])

  const clearBackoffTimer = useCallback(() => {
    if (backoffTimerRef.current) {
      clearTimeout(backoffTimerRef.current)
      backoffTimerRef.current = null
    }
  }, [])

  // Read one SSE stream to completion. Returns true if the stream completed normally (DONE or
  // review-completed), false on 409 (caller decides whether to back off and retry).
  const consumeStream = useCallback(async ({ resume, supplement }) => {
    const controller = new AbortController()
    abortControllerRef.current = controller
    const runId = runIdRef.current
    const url = `/api/projects/${encodeURIComponent(projectId)}/independent-review/stream`

    let response
    try {
      response = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resume: !!resume, run_id: runId, supplement: supplement || undefined }),
        signal: controller.signal,
      })
    } catch (err) {
      if (err.name === 'AbortError') return true
      applyEvent({ type: 'error', message: err.message || '网络错误' })
      return true
    }

    if (response.status === 409) {
      // Previous run still finishing — signal the caller to back off + retry.
      return false
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }))
      // detail 可能是 FastAPI 422 校验数组 / 对象——必须归一成字符串再进 windowState.error，
      // 否则渲染「错误：{windowState.error}」会把数组/对象塞进 React 子节点导致整窗崩溃（codex spec BLOCKER）。
      applyEvent({ type: 'error', message: normalizeApiErrorDetail(body?.detail, '启动审查失败') })
      return true
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let sawError = false
    let reachedDone = false
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() || ''
        for (const block of blocks) {
          if (!block.startsWith('data: ')) continue
          const payload = block.slice(6)
          if (payload === '[DONE]') { reachedDone = true; continue }
          const event = parseDrawerEvent(payload)
          if (!event) continue
          if (event.type === 'review-completed') {
            // report_mtime_ns stays the opaque string it arrived as (never Number()).
            completedRef.current = true
            applyEvent(event)
            onCompletedRef.current?.({ run_id: event.run_id, report_mtime_ns: event.report_mtime_ns })
            // Auto-close WITHOUT /discard — discard would clear the done tombstone and break the
            // reporting turn's run-bound check.
            onCloseRef.current?.()
            return true
          }
          if (event.type === 'error') sawError = true
          applyEvent(event)
        }
        if (reachedDone) break
      }
      // Stream ended (server [DONE] or raw EOF). A normal success already returned above via
      // review-completed. If we reach here WITHOUT a completion and WITHOUT an explicit error,
      // the review broke mid-flight (disconnect → backend breaks the generator with no error
      // event; or worker ended with no valid done tombstone). Surface a RESUMABLE error so the
      // window leaves 'running' and offers 继续审查 — never let a dropped review look "done".
      // (R1 core: a broken review must be resumable from where it stopped, not silently lost.)
      if (!completedRef.current && !sawError) {
        applyEvent({ type: 'error', message: '连接已中断或审查未完成，可继续审查。' })
      }
      return true
    } catch (err) {
      if (err.name === 'AbortError') return true
      applyEvent({ type: 'error', message: err.message || '网络错误' })
      return true
    }
  }, [projectId, applyEvent])

  // Run the stream with 409 exponential backoff up to a cap, then stop and offer the user a way
  // out (the errored state surfaces 重新发起 / 关闭).
  const runWithBackoff = useCallback(async ({ resume, supplement }) => {
    let attempt = 0
    while (true) {
      if (closingRef.current) return
      const completed = await consumeStream({ resume, supplement })
      if (completed) return
      // 409: back off and retry, unless we've exhausted attempts.
      if (backoffExhausted(attempt)) {
        applyEvent({ type: 'error', message: '上一次审查仍在收尾，请稍后重新发起。' })
        return
      }
      const delay = nextBackoff(attempt)
      attempt += 1
      await new Promise(resolve => {
        backoffTimerRef.current = setTimeout(resolve, delay)
      })
    }
  }, [consumeStream, applyEvent])

  // Open: generate a stable run_id and kick off the first (non-resume) stream — but ONLY on the
  // isOpen rising edge (see openedRef). A projectId change while open must NOT restart the review.
  useEffect(() => {
    const justOpened = isOpen && !openedRef.current
    openedRef.current = isOpen
    if (!isOpen || !projectId) return
    if (!justOpened) return
    closingRef.current = false
    completedRef.current = false
    runIdRef.current = genRunId()
    setBubbles([])
    setWindowState(initialReviewWindowState())
    runWithBackoff({ resume: false })
    return () => {
      clearBackoffTimer()
      abortControllerRef.current?.abort()
    }
  }, [isOpen, projectId, runWithBackoff, clearBackoffTimer])

  // Active close (button / ESC): abort the in-flight fetch + discard the session + close.
  const handleActiveClose = useCallback(() => {
    closingRef.current = true
    clearBackoffTimer()
    abortControllerRef.current?.abort()
    const runId = runIdRef.current
    // Only discard if the run did NOT already complete — discarding after completion would clear
    // the done tombstone and break the main agent's run-bound reporting turn.
    if (runId && !completedRef.current) {
      fetch(`/api/projects/${encodeURIComponent(projectId)}/independent-review/discard`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }),
      }).catch(() => {})
    }
    onCloseRef.current?.()
  }, [projectId, clearBackoffTimer])

  // 继续审查: resume from where the errored run left off, optionally carrying a user supplement
  // typed in the errored panel. The supplement is sent in the POST body (consumeStream already
  // forwards it) so the agent picks up with the accumulated context + the user's correction.
  const handleResume = useCallback(() => {
    if (closingRef.current) return
    const supplement = supplementInput.trim() || undefined
    applyEvent({ type: 'resume-start' })
    setSupplementInput('')
    runWithBackoff({ resume: true, supplement })
  }, [applyEvent, runWithBackoff, supplementInput])

  // 重新发起: after backoff exhaustion, retry the resume.
  const handleRestart = useCallback(() => {
    handleResume()
  }, [handleResume])

  useEffect(() => {
    if (!isOpen) return
    const handleKeydown = (event) => {
      if (event.key === 'Escape') handleActiveClose()
    }
    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [isOpen, handleActiveClose])

  // Draggable header.
  const handleDragStart = useCallback((event) => {
    const startX = event.clientX
    const startY = event.clientY
    const origin = dragRef.current?.getBoundingClientRect()
    const baseX = position.x == null ? (origin?.left ?? 0) : position.x
    const baseY = position.y == null ? (origin?.top ?? 0) : position.y
    const onMove = (moveEvent) => {
      setPosition({ x: baseX + (moveEvent.clientX - startX), y: baseY + (moveEvent.clientY - startY) })
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [position])

  if (!isOpen) return null

  const positioned = position.x != null && position.y != null
  const style = positioned ? { left: position.x, top: position.y, right: 'auto', bottom: 'auto' } : undefined

  const windowEl = (
    <div
      ref={dragRef}
      style={isMobile ? { height: '100dvh' } : style}
      className={isMobile
        ? "fixed inset-0 w-full bg-card z-50 flex flex-col"
        : "fixed bottom-4 right-4 w-[480px] h-[600px] bg-card border border-border rounded-win shadow-float z-50 flex flex-col"}
    >
      <div
        onMouseDown={isMobile ? undefined : handleDragStart}
        className={isMobile
          ? "px-4 py-3 border-b border-border flex items-center justify-between select-none"
          : "px-4 py-3 border-b border-border flex items-center justify-between cursor-move select-none"}
      >
        <div className={isMobile ? "flex flex-col min-w-0" : "flex flex-col"}>
          <span className="text-sm font-medium text-text">
            {windowState.status === 'completed'
              ? '审查完成'
              : windowState.status === 'errored'
                ? '审查已暂停'
                : '独立审查代理工作中…'}
          </span>
          <span className="text-xs text-t2">
            第 {windowState.round} 轮 · {windowState.action}
          </span>
        </div>
        <button
          type="button"
          onClick={handleActiveClose}
          className={isMobile
            ? "text-t2 hover:text-text text-lg leading-none px-2 whitespace-nowrap flex-shrink-0"
            : "text-t2 hover:text-text text-lg leading-none px-2"}
          title={isMobile ? "停止审查" : "关闭"}
          aria-label={isMobile ? "停止审查" : "关闭"}
        >
          {isMobile ? '停止审查' : '×'}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-2 text-sm text-text">
        {bubbles.map((bubble, i) => {
          if (bubble.kind === 'assistant') {
            return (
              <div key={i} className="selectable-content">
                <MarkdownMessage>{bubble.text}</MarkdownMessage>
              </div>
            )
          }
          if (bubble.kind === 'tool') {
            // 统一用共享 ToolCallPill 渲染（与主聊天一致）；bubble 已含 {id,tool,arg,status,summary}。
            return <ToolCallPill key={bubble.id ?? `tool-${i}`} event={bubble} />
          }
          return null
        })}
        {windowState.status === 'errored' && (
          <div className="mt-4 rounded-card border border-error/40 bg-error/10 p-3 text-error">
            <div>错误：{windowState.error}</div>
            <textarea
              value={supplementInput}
              onChange={e => setSupplementInput(e.target.value)}
              placeholder="可补充审查重点或修正方向（可选）；直接点「继续审查」则从断处接着审"
              rows={2}
              className="mt-2 w-full rounded-btn bg-field border border-border px-2 py-1 text-xs text-text placeholder-t3 resize-none focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={handleRestart}
                className="px-3 py-1 rounded-btn bg-accent text-white text-xs hover:bg-accent/90"
              >
                继续审查
              </button>
              <button
                type="button"
                onClick={handleActiveClose}
                className="px-3 py-1 rounded-btn bg-card2 border border-border text-t2 text-xs hover:bg-asoft"
              >
                关闭
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
  return isMobile ? createPortal(windowEl, document.body) : windowEl
}
