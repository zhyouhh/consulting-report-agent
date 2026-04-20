import React, { useEffect, useId, useRef } from 'react'

/**
 * Generic confirmation dialog with basic a11y:
 *   - role="dialog" + aria-modal="true"
 *   - aria-labelledby points at the title
 *   - ESC closes the dialog
 *   - Focus moves to the cancel button on open (safe default — avoids
 *     accidental confirmation)
 *
 * Props:
 *   open        {boolean}
 *   title       {string}
 *   body        {string}  — supports \n as line break
 *   confirmText {string}  default "确认"
 *   cancelText  {string}  default "取消"
 *   onConfirm   {() => void}
 *   onCancel    {() => void}
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmText = '确认',
  cancelText = '取消',
  onConfirm,
  onCancel,
}) {
  const titleId = useId()
  const cancelRef = useRef(null)
  const previouslyFocusedRef = useRef(null)

  // ESC to close
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCancel?.()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onCancel])

  // Focus management: move focus to cancel button on open; restore on close
  useEffect(() => {
    if (open) {
      previouslyFocusedRef.current = document.activeElement
      // Defer focus until after render so the button is mounted
      const id = window.setTimeout(() => {
        cancelRef.current?.focus()
      }, 0)
      return () => window.clearTimeout(id)
    }
    // On close, restore focus to the previously focused element
    if (previouslyFocusedRef.current && typeof previouslyFocusedRef.current.focus === 'function') {
      try {
        previouslyFocusedRef.current.focus()
      } catch {
        // ignore — element may have been removed
      }
    }
    previouslyFocusedRef.current = null
    return undefined
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-[#1e2140] border border-[#3a3f6d] rounded-2xl p-6 max-w-sm w-full mx-4 shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <h3 id={titleId} className="text-base font-semibold text-[#eef1ff] mb-3">{title}</h3>
        <p className="text-sm text-[#a8accc] leading-relaxed whitespace-pre-line mb-6">{body}</p>
        <div className="flex gap-3 justify-end">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm bg-[#262a4c] text-[#8f93c9] hover:bg-[#30365a] focus:outline-none focus:ring-2 focus:ring-[#6070e0]"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg text-sm bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] focus:outline-none focus:ring-2 focus:ring-[#6070e0]"
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
