import { useEffect, useRef, useState } from 'react'
import { parseDrawerEvent } from '../utils/independentReviewDrawer'

export default function IndependentReviewDrawer({
  projectId,
  isOpen,
  onClose,
  onCompleted,
}) {
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const abortControllerRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return

    const handleKeydown = (event) => {
      if (event.key === 'Escape') {
        abortControllerRef.current?.abort()
        onClose?.()
      }
    }

    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [isOpen, onClose])

  useEffect(() => {
    if (!isOpen || !projectId) return
    setEvents([])
    setError(null)

    const controller = new AbortController()
    abortControllerRef.current = controller

    const runStream = async () => {
      try {
        const url = `/api/projects/${encodeURIComponent(projectId)}/independent-review/stream`
        const response = await fetch(url, { method: 'GET', signal: controller.signal })
        if (!response.ok) {
          const detail = await response.json().catch(() => ({ detail: response.statusText }))
          throw new Error(detail.detail || '启动审查失败')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() || ''

          for (const block of blocks) {
            if (!block.startsWith('data: ')) continue
            const payload = block.slice(6)
            if (payload === '[DONE]') return

            const data = parseDrawerEvent(payload)
            if (!data) continue

            if (data.type === 'review-completed') {
              onCompleted?.(data.path)
              onClose?.()
              return
            }
            if (data.type === 'error') {
              setError(data.detail || '审查失败')
              setTimeout(() => onClose?.(), 3000)
              return
            }
            setEvents(prev => [...prev, data])
          }
        }
      } catch (err) {
        if (err.name === 'AbortError') return
        setError(err.message || '网络错误')
        setTimeout(() => onClose?.(), 3000)
      }
    }

    runStream()
    return () => controller.abort()
  }, [isOpen, projectId, onClose, onCompleted])

  if (!isOpen) return null

  return (
    <div className="fixed bottom-4 right-4 w-[480px] h-[600px] bg-[#171a31] border border-[#2f3158] rounded-2xl shadow-2xl z-50 flex flex-col">
      <div className="px-4 py-3 border-b border-[#2f3158] flex items-center justify-between">
        <span className="text-sm font-medium text-[#eef1ff]">独立审查代理工作中...</span>
        <span className="text-xs text-[#8f93c9]">{events.length} 步</span>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-2 text-sm text-[#d9dcf5]">
        {events.map((evt, i) => <DrawerEvent key={i} event={evt} />)}
        {error && <div className="text-red-400 mt-4">错误：{error}</div>}
      </div>
    </div>
  )
}

function DrawerEvent({ event }) {
  if (event.type === 'progress') return <div className="text-[#64ffda]">▸ {event.detail}</div>
  if (event.type === 'tool_call') return <div className="text-[#8f93c9]">工具：{event.tool}</div>
  if (event.type === 'tool_result') return <div className="text-[#8f93c9]">结果：{event.summary}</div>
  if (event.type === 'content') return <div className="text-[#d9dcf5]">{event.text}</div>
  return null
}
