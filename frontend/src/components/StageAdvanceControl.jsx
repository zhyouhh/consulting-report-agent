import React, { useState } from 'react'
import axios from 'axios'
import ConfirmDialog from './ConfirmDialog'
import { isS4ReviewButtonVisible, isS1ConfirmOutlineEnabled } from '../utils/workspaceSummary'
import { showError } from '../utils/toast'

/**
 * §9.1 / §9.2 stage-advance button area.
 *
 * Props:
 *   projectId       {string}
 *   summary         {object}  — from summarizeWorkspace()
 *   onCheckpointSet {() => void} — called after successful checkpoint POST; triggers loadWorkspace
 *   onInsertPrompt  {(text: string) => void} — inserts text into chat input (S4 "继续扩写")
 */
export default function StageAdvanceControl({ projectId, summary, onCheckpointSet, onInsertPrompt }) {
  const [confirmState, setConfirmState] = useState(null) // { title, body, onConfirm }
  const [pending, setPending] = useState(false)

  const { stageCode, wordCount, lengthTargets } = summary

  // Helper: POST checkpoint with user-visible error feedback.
  // Returns true on success, false on failure (caller can skip follow-ups).
  const postCheckpoint = async (name, action = 'set') => {
    if (pending) return false
    setPending(true)
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
    } finally {
      setPending(false)
    }
  }

  const openConfirm = (title, body, onConfirm) => {
    setConfirmState({ title, body, onConfirm })
  }

  const closeConfirm = () => setConfirmState(null)

  // ── S1 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S1') {
    const outlineExists = isS1ConfirmOutlineEnabled(summary)
    return (
      <div className="mt-4">
        <button
          onClick={() => postCheckpoint('outline-confirmed')}
          disabled={!outlineExists || pending}
          className={`w-full py-2.5 px-4 rounded-xl text-sm font-medium transition-colors ${
            outlineExists && !pending
              ? 'bg-[#3b4fa8] text-white hover:bg-[#4a5fcc]'
              : 'bg-[#1e2140] text-[#4a4f72] cursor-not-allowed'
          }`}
        >
          {pending ? '处理中…' : '确认大纲，进入资料采集'}
        </button>
        {!outlineExists && !pending && (
          <p className="mt-2 text-xs text-[#5a5e80] text-center">需要先生成大纲才能继续</p>
        )}
      </div>
    )
  }

  // ── S2 / S3 ─ no advance button ─────────────────────────────────────────
  if (stageCode === 'S2' || stageCode === 'S3') {
    return null
  }

  // ── S4 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S4') {
    const reviewVisible = isS4ReviewButtonVisible(wordCount, lengthTargets)
    const targetVal = lengthTargets?.target ?? 0

    return (
      <div className="mt-4 space-y-2">
        <div className="flex gap-2">
          <button
            onClick={() => onInsertPrompt?.('请继续扩写正文')}
            className={`flex-1 py-2.5 px-4 rounded-xl text-sm font-medium transition-colors ${
              reviewVisible
                ? 'bg-[#262a4c] text-[#a8accc] hover:bg-[#30365a]'
                : 'bg-[#3b4fa8] text-white hover:bg-[#4a5fcc]'
            }`}
          >
            继续扩写
          </button>
          {reviewVisible && (
            <button
              onClick={() => postCheckpoint('review-started')}
              disabled={pending}
              className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {pending ? '处理中…' : '完成撰写，开始审查'}
            </button>
          )}
        </div>
        {targetVal > 0 && (
          <p className="text-xs text-[#5a5e80] text-center">
            当前 {wordCount} 字 / 目标 {targetVal} 字
          </p>
        )}
      </div>
    )
  }

  // ── S5 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S5') {
    return (
      <>
        <div className="mt-4 flex gap-2">
          <button
            onClick={() => postCheckpoint('review-passed')}
            disabled={pending}
            className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {pending ? '处理中…' : '审查通过，准备交付'}
          </button>
          <button
            onClick={() =>
              openConfirm(
                '确认回去继续改报告？',
                '你写好的正文内容不会被删除，只是重新打开修改通道。',
                async () => {
                  const ok = await postCheckpoint('review-started', 'clear')
                  if (ok) closeConfirm()
                }
              )
            }
            disabled={pending}
            className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#262a4c] text-[#a8accc] hover:bg-[#30365a] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            回去再改
          </button>
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

  // ── S6 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S6') {
    return (
      <div className="mt-4">
        <button
          onClick={() => postCheckpoint('presentation-ready')}
          disabled={pending}
          className="w-full py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {pending ? '处理中…' : '演示准备完成'}
        </button>
      </div>
    )
  }

  // ── S7 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S7') {
    return (
      <div className="mt-4">
        <button
          onClick={() => postCheckpoint('delivery-archived')}
          disabled={pending}
          className="w-full py-2.5 px-4 rounded-xl text-sm font-medium bg-[#26315d] text-[#a8accc] hover:bg-[#32407a] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {pending ? '处理中…' : '归档，结束项目'}
        </button>
      </div>
    )
  }

  // S0 or unknown → nothing
  return null
}
