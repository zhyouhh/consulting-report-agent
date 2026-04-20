import React, { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import ConfirmDialog from './ConfirmDialog'
import {
  ROLLBACK_HIDDEN_STAGES,
  getFirstLevelOption,
  OPTION_KIND_INSERT_PROMPT,
  OPTION_KIND_CLEAR_CHECKPOINT,
  OPTION_KIND_NOOP,
} from '../utils/rollbackMenuLogic'

/**
 * §9.4 "⋯" rollback menu.
 *
 * Visibility: only shown for stages S2 and later (stageCode >= S2).
 * S5 primary level is empty (secondary button handles "回去再改" already).
 *
 * Props:
 *   projectId       {string}
 *   stageCode       {string}
 *   onCheckpointSet {() => void}
 *   onInsertPrompt  {(text: string) => void}  — for "调整大纲" (S2/S3)
 *   onRequestError  {(message: string) => void} — surface POST failures
 */

export { getFirstLevelOption } from '../utils/rollbackMenuLogic'

export default function RollbackMenu({ projectId, stageCode, onCheckpointSet, onInsertPrompt, onRequestError }) {
  const [open, setOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [confirmState, setConfirmState] = useState(null)
  const menuRef = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false)
        setAdvancedOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  if (ROLLBACK_HIDDEN_STAGES.has(stageCode) || !stageCode) return null

  const postCheckpoint = async (name, action) => {
    try {
      await axios.post(
        `/api/projects/${encodeURIComponent(projectId)}/checkpoints/${name}?action=${action}`
      )
      onCheckpointSet?.()
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || '请稍后重试'
      onRequestError?.(`操作失败：${detail}`)
    }
  }

  const openConfirm = (title, body, onConfirm) => {
    setOpen(false)
    setAdvancedOpen(false)
    setConfirmState({ title, body, onConfirm })
  }

  const closeConfirm = () => setConfirmState(null)

  const firstLevel = getFirstLevelOption(stageCode)

  const handleFirstLevelClick = () => {
    if (!firstLevel) return
    if (firstLevel.kind === OPTION_KIND_INSERT_PROMPT) {
      onInsertPrompt?.(firstLevel.prompt)
      setOpen(false)
      return
    }
    if (firstLevel.kind === OPTION_KIND_NOOP) {
      setOpen(false)
      return
    }
    if (firstLevel.kind === OPTION_KIND_CLEAR_CHECKPOINT) {
      openConfirm(
        firstLevel.confirmTitle,
        firstLevel.confirmBody,
        async () => {
          await postCheckpoint(firstLevel.checkpoint, firstLevel.action)
          closeConfirm()
        }
      )
    }
  }

  return (
    <>
      <div className="relative" ref={menuRef}>
        <button
          onClick={() => { setOpen(v => !v); setAdvancedOpen(false) }}
          className="p-1.5 rounded-lg text-[#5a5e80] hover:text-[#a8accc] hover:bg-[#1e2140] transition-colors"
          title="更多选项"
        >
          <span className="text-lg leading-none select-none">⋯</span>
        </button>

        {open && (
          <div className="absolute right-0 top-8 z-40 w-56 rounded-xl border border-[#2f3158] bg-[#1a1e3a] shadow-xl py-1">

            {/* First-level option */}
            {firstLevel ? (
              <button
                onClick={handleFirstLevelClick}
                className="w-full text-left px-4 py-2.5 text-sm text-[#c8ccee] hover:bg-[#222645] transition-colors"
              >
                {firstLevel.label}
              </button>
            ) : (
              /* S5: first level is empty but menu still opens for advanced section */
              null
            )}

            {/* Divider before advanced section */}
            {firstLevel && <div className="my-1 border-t border-[#2a2e52]" />}

            {/* §9.4 Advanced section — disclosure icon + grey text, NOT a button */}
            <button
              onClick={() => setAdvancedOpen(v => !v)}
              className="w-full text-left px-4 py-2 flex items-center gap-1.5 text-xs text-[#5a5e80] hover:text-[#8f93c9] transition-colors"
            >
              <span className={`transition-transform duration-150 ${advancedOpen ? 'rotate-90' : ''}`}>▸</span>
              更多回退选项
            </button>

            {advancedOpen && (
              <div className="mt-1">
                {/* "完全重置大纲确认" */}
                <button
                  onClick={() =>
                    openConfirm(
                      '确认重置大纲确认？',
                      '你写好的报告正文不会被删除，但暂时无法继续修改，\n直到重新确认新的大纲后才能继续写。',
                      async () => {
                        await postCheckpoint('outline-confirmed', 'clear')
                        closeConfirm()
                      }
                    )
                  }
                  className="w-full text-left px-6 py-2 text-sm text-[#c8ccee] hover:bg-[#222645] transition-colors"
                >
                  完全重置大纲确认
                </button>

                {/* "撤回归档" — only relevant when archived */}
                <button
                  onClick={() =>
                    openConfirm(
                      '确认撤回归档？',
                      '所有文件都会保留，只是项目重新回到待归档状态。',
                      async () => {
                        await postCheckpoint('delivery-archived', 'clear')
                        closeConfirm()
                      }
                    )
                  }
                  className="w-full text-left px-6 py-2 text-sm text-[#c8ccee] hover:bg-[#222645] transition-colors"
                >
                  撤回归档
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={!!confirmState}
        title={confirmState?.title}
        body={confirmState?.body}
        onConfirm={confirmState?.onConfirm}
        onCancel={closeConfirm}
      />
    </>
  )
}
