import React, { useState } from 'react'
import axios from 'axios'
import ConfirmDialog from './ConfirmDialog'
import { isS4ReviewButtonVisible, isS1ConfirmOutlineEnabled } from '../utils/workspaceSummary'

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

  const { stageCode, wordCount, lengthTargets } = summary

  // Helper: POST checkpoint
  const postCheckpoint = async (name, action = 'set') => {
    await axios.post(
      `/api/projects/${encodeURIComponent(projectId)}/checkpoints/${name}?action=${action}`
    )
    onCheckpointSet?.()
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
          disabled={!outlineExists}
          className={`w-full py-2.5 px-4 rounded-xl text-sm font-medium transition-colors ${
            outlineExists
              ? 'bg-[#3b4fa8] text-white hover:bg-[#4a5fcc]'
              : 'bg-[#1e2140] text-[#4a4f72] cursor-not-allowed'
          }`}
        >
          确认大纲，进入资料采集
        </button>
        {!outlineExists && (
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
              className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors"
            >
              完成撰写，开始审查
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
            className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors"
          >
            审查通过，准备交付
          </button>
          <button
            onClick={() =>
              openConfirm(
                '确认回去继续改报告？',
                '你写好的正文内容不会被删除，只是重新打开修改通道。',
                async () => {
                  await postCheckpoint('review-started', 'clear')
                  closeConfirm()
                }
              )
            }
            className="flex-1 py-2.5 px-4 rounded-xl text-sm font-medium bg-[#262a4c] text-[#a8accc] hover:bg-[#30365a] transition-colors"
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
          className="w-full py-2.5 px-4 rounded-xl text-sm font-medium bg-[#3b4fa8] text-white hover:bg-[#4a5fcc] transition-colors"
        >
          演示准备完成
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
          className="w-full py-2.5 px-4 rounded-xl text-sm font-medium bg-[#26315d] text-[#a8accc] hover:bg-[#32407a] transition-colors"
        >
          归档，结束项目
        </button>
      </div>
    )
  }

  // S0 or unknown → nothing
  return null
}
