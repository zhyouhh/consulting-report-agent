import React from 'react'

/**
 * Generic confirmation dialog.
 * Props:
 *   open       {boolean}
 *   title      {string}
 *   body       {string}  — supports \n as line break
 *   confirmText {string} default "确认"
 *   cancelText  {string} default "取消"
 *   onConfirm  {() => void}
 *   onCancel   {() => void}
 */
export default function ConfirmDialog({ open, title, body, confirmText = '确认', cancelText = '取消', onConfirm, onCancel }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onCancel}>
      <div
        className="bg-[#1e2140] border border-[#3a3f6d] rounded-2xl p-6 max-w-sm w-full mx-4 shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-[#eef1ff] mb-3">{title}</h3>
        <p className="text-sm text-[#a8accc] leading-relaxed whitespace-pre-line mb-6">{body}</p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg text-sm bg-[#262a4c] text-[#8f93c9] hover:bg-[#30365a]"
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg text-sm bg-[#3b4fa8] text-white hover:bg-[#4a5fcc]"
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}
