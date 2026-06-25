import React, { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import ConfirmDialog from './ConfirmDialog'
import { showError } from '../utils/toast'
import {
  ROLLBACK_HIDDEN_STAGES,
  getFirstLevelOption,
  getAdvancedRollbackOptions,
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
 */

export { getFirstLevelOption } from '../utils/rollbackMenuLogic'

export default function RollbackMenu({ projectId, stageCode, onCheckpointSet, onInsertPrompt }) {
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
      return true
    } catch (err) {
      const detail = err?.response?.data?.detail || err?.message || '请稍后重试'
      showError(`操作失败：${detail}`)
      return false
    }
  }

  const openConfirm = (title, body, onConfirm) => {
    setOpen(false)
    setAdvancedOpen(false)
    setConfirmState({ title, body, onConfirm })
  }

  const closeConfirm = () => setConfirmState(null)

  const firstLevel = getFirstLevelOption(stageCode)
  const advancedOptions = getAdvancedRollbackOptions(stageCode)

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
          const ok = await postCheckpoint(firstLevel.checkpoint, firstLevel.action)
          if (ok) closeConfirm()
        }
      )
    }
  }

  return (
    <>
      <div className="relative" ref={menuRef}>
        <button
          onClick={() => { setOpen(v => !v); setAdvancedOpen(false) }}
          className="w-[28px] h-[28px] flex items-center justify-center border border-border rounded-ibtn bg-card2 text-t3 hover:text-text transition-colors"
          title="更多选项"
        >
          <svg width="16" height="4" viewBox="0 0 16 4" fill="currentColor" aria-hidden="true">
            <circle cx="2" cy="2" r="1.5"/>
            <circle cx="8" cy="2" r="1.5"/>
            <circle cx="14" cy="2" r="1.5"/>
          </svg>
        </button>

        {open && (
          <div className="absolute right-0 top-[34px] z-40 w-[226px] bg-card border border-border rounded-card shadow-popover p-[5px]">

            {/* First-level option */}
            {firstLevel ? (
              <button
                onClick={handleFirstLevelClick}
                className="w-full text-left px-3 py-[9px] rounded-ibtn text-13 text-text hover:bg-card2 transition-colors"
              >
                {firstLevel.label}
              </button>
            ) : (
              /* S5: first level is empty but menu still opens for advanced section */
              null
            )}

            {/* Divider before advanced section */}
            {firstLevel && <div className="h-px bg-hair mx-1.5 my-1" />}

            {/* §9.4 Advanced section — disclosure icon + grey text, NOT a button */}
            <button
              onClick={() => setAdvancedOpen(v => !v)}
              className="w-full text-left px-3 py-[7px] rounded-ibtn text-12 text-t3 hover:text-text flex items-center gap-[7px] transition-colors"
            >
              <span className={`transition-transform duration-150 ${advancedOpen ? 'rotate-90' : ''}`}>▸</span>
              更多回退选项
            </button>

            {advancedOpen && (
              <div className="mt-1">
                {/* S2+: s0 interview rollback from getAdvancedRollbackOptions */}
                {advancedOptions.map((opt) => (
                  <button
                    key={opt.checkpoint}
                    onClick={() => {
                      if (opt.kind === OPTION_KIND_CLEAR_CHECKPOINT) {
                        openConfirm(
                          opt.confirmTitle,
                          opt.confirmBody,
                          async () => {
                            const ok = await postCheckpoint(opt.checkpoint, opt.action)
                            if (ok) closeConfirm()
                          }
                        )
                      }
                    }}
                    className="w-full text-left pl-[26px] pr-3 py-2 rounded-ibtn text-13 text-text hover:bg-card2 transition-colors"
                  >
                    {opt.label}
                  </button>
                ))}

                {/* Divider before legacy advanced options */}
                {advancedOptions.length > 0 && (
                  <div className="h-px bg-hair mx-1.5 my-1" />
                )}

                {/* "完全重置大纲确认" */}
                <button
                  onClick={() =>
                    openConfirm(
                      '确认重置大纲确认？',
                      '你写好的报告正文不会被删除，但暂时无法继续修改，\n直到重新确认新的大纲后才能继续写。',
                      async () => {
                        const ok = await postCheckpoint('outline-confirmed', 'clear')
                        if (ok) closeConfirm()
                      }
                    )
                  }
                  className="w-full text-left pl-[26px] pr-3 py-2 rounded-ibtn text-13 text-text hover:bg-card2 transition-colors"
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
                        const ok = await postCheckpoint('delivery-archived', 'clear')
                        if (ok) closeConfirm()
                      }
                    )
                  }
                  className="w-full text-left pl-[26px] pr-3 py-2 rounded-ibtn text-13 text-text hover:bg-card2 transition-colors"
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
