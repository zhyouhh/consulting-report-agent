import React, { useState } from 'react'
import axios from 'axios'
import ConfirmDialog from './ConfirmDialog'
import { isS4ReviewButtonVisible, isS1ConfirmOutlineEnabled, s1ConfirmDisabledReason } from '../utils/workspaceSummary'
import { showError } from '../utils/toast'

/**
 * §9.1 / §9.2 stage-advance button area.
 *
 * Props:
 *   projectId       {string}
 *   summary         {object}  — from summarizeWorkspace()
 *   onCheckpointSet {() => void} — called after successful checkpoint POST; triggers loadWorkspace
 *   onInsertPrompt  {(text: string) => void} — inserts text into chat input (S4 "继续扩写")
 *   onSendPrompt    {(text: string) => boolean} — 代用户发一条确认消息走主模型（S1/S7 代发自愈）；
 *                   聊天忙时返回 false，由本组件给出提示
 *
 * 2026-07-06 反馈①：S1「确认大纲」/ S7「归档」从直连 checkpoint API 改为代发消息走主模型——
 * 直连撞后端门禁（缺 research-plan.md / delivery-log.md）是 400 死路；代发让模型看到门禁报错后
 * 自己补齐缺失文件再推进（与用户打字确认同路径、可自愈）。S4/S5 保持直连：S4 卡内容阈值、
 * S5 卡独立审查报告（主模型禁写），代发救不了、直连报错文案本就指向正确按钮。S6 演示功能未做，不动。
 */
export default function StageAdvanceControl({ projectId, summary, onCheckpointSet, onInsertPrompt, onSendPrompt, stageToolsRunning = false }) {
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

  // Helper: 代用户发确认消息（S1/S7）。忙时（正在回复/上传）提示稍候，不静默丢弃。
  const sendConfirmMessage = (text) => {
    const ok = onSendPrompt?.(text) ?? false
    if (!ok) showError('助手正在处理中，请等本轮回复结束后再试')
    return ok
  }

  const openConfirm = (title, body, onConfirm) => {
    setConfirmState({ title, body, onConfirm })
  }

  const closeConfirm = () => setConfirmState(null)

  // ── S1 ──────────────────────────────────────────────────────────────────
  if (stageCode === 'S1') {
    const outlineExists = isS1ConfirmOutlineEnabled(summary)
    const disabledReason = s1ConfirmDisabledReason(summary)
    return (
      <div className="mt-4">
        <button
          onClick={() => sendConfirmMessage('我确认当前大纲，请进入下一阶段开始资料采集。')}
          disabled={!outlineExists || pending}
          className={`w-full py-2.5 px-4 rounded-btn text-13 font-medium transition-colors ${
            outlineExists && !pending
              ? 'bg-accent text-white hover:bg-accent/90'
              : 'bg-card2 text-t3 cursor-not-allowed'
          }`}
        >
          {pending ? '处理中…' : '确认大纲，进入资料采集'}
        </button>
        {!outlineExists && !pending && disabledReason && (
          <p className="mt-2 text-xs text-t3 text-center">{disabledReason}</p>
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
    const targetVal = lengthTargets?.expected_length ?? 0

    return (
      <div className="mt-4 space-y-2">
        <div className="flex gap-2">
          <button
            onClick={() => onInsertPrompt?.('请继续扩写正文')}
            className={`flex-1 py-2.5 px-4 rounded-btn text-13 font-medium transition-colors ${
              reviewVisible
                ? 'border border-col bg-card2 text-text hover:bg-asoft'
                : 'bg-accent text-white hover:bg-accent/90'
            }`}
          >
            继续扩写
          </button>
          {reviewVisible && (
            <button
              onClick={() => postCheckpoint('review-started')}
              disabled={pending}
              className="flex-1 py-2.5 px-4 rounded-btn text-13 font-medium bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {pending ? '处理中…' : '完成撰写，开始审查'}
            </button>
          )}
        </div>
        {targetVal > 0 && (
          <p className="text-xs text-t3 text-center">
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
            disabled={pending || stageToolsRunning}
            className="flex-1 py-2.5 px-4 rounded-btn text-13 font-medium bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
            disabled={pending || stageToolsRunning}
            className="flex-1 py-2.5 px-4 rounded-btn text-13 font-medium border border-col bg-card2 text-text hover:bg-asoft transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
          className="w-full py-2.5 px-4 rounded-btn text-13 font-medium bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
          onClick={() => sendConfirmMessage('我确认报告已交付，请完成收尾并归档结束项目。')}
          disabled={pending}
          className="w-full py-2.5 px-4 rounded-btn text-13 font-medium bg-accent text-white hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {pending ? '处理中…' : '归档，结束项目'}
        </button>
      </div>
    )
  }

  // S0 or unknown → nothing
  return null
}
