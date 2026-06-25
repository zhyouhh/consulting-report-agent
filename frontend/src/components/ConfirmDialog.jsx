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
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="bg-card border border-border rounded-win p-6 max-w-sm w-full mx-4 shadow-popover"
        onClick={e => e.stopPropagation()}
      >
        <h3 id={titleId} className="text-base font-semibold text-text mb-3">{title}</h3>
        <p className="text-sm text-t2 leading-relaxed whitespace-pre-line mb-6">{body}</p>
        <div className="flex gap-3 justify-end">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="px-4 py-2 rounded-btn text-sm bg-card2 text-t2 hover:bg-asoft focus:outline-none focus:ring-2 focus:ring-accent"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-btn text-sm bg-accent text-white hover:bg-accent/90 focus:outline-none focus:ring-2 focus:ring-accent"
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
