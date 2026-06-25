import React from 'react'
import { summarizeWorkspace, shouldShowPresentationStage, getStageName } from '../utils/workspaceSummary'
import { getStageButtonState } from '../utils/stagePanelButtons'
import StageAdvanceControl from './StageAdvanceControl'
import RollbackMenu from './RollbackMenu'
import { IconCheck } from './icons'

// ── §9.6 Progress bar ────────────────────────────────────────────────────────
// Labels come from the single STAGE_NAMES source of truth (see
// workspaceSummary.js) so any rename lands in one place.

const REPORT_ONLY_CODES = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S7']
const REPORT_AND_PRESENTATION_CODES = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7']

const REPORT_ONLY_STAGES = REPORT_ONLY_CODES.map(code => ({ code, label: getStageName(code) }))
const REPORT_AND_PRESENTATION_STAGES = REPORT_AND_PRESENTATION_CODES.map(code => ({ code, label: getStageName(code) }))

const STAGE_ORDER = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'done']

function getStageIndex(code) {
  return STAGE_ORDER.indexOf(code)
}

function Stepper({ stageCode, deliveryMode }) {
  const stages = shouldShowPresentationStage(deliveryMode)
    ? REPORT_AND_PRESENTATION_STAGES
    : REPORT_ONLY_STAGES

  const currentIdx = getStageIndex(stageCode)
  const isDone = stageCode === 'done'
  const total = stages.length

  // Compute fill percentage: from first dot to current dot
  // Each dot is at position i/(total-1) * 100%
  const currentDotPos = isDone
    ? 100
    : total > 1 ? (currentIdx / (total - 1)) * 100 : 0
  const stepPct = currentDotPos

  return (
    <div className="relative mt-[18px] px-1">
      {/* Track line */}
      <div className="absolute left-[11px] right-[11px] top-[6px] h-[2px] bg-track" />
      {/* Fill line */}
      <div
        className="absolute left-[11px] top-[6px] h-[2px] bg-abright transition-all duration-500"
        style={{ width: `calc(${stepPct}% * (100% - 22px) / 100%)` }}
      />
      {/* Dots row */}
      <div className="relative flex justify-between">
        {stages.map(({ code, label }, i) => {
          const segIdx = getStageIndex(code)
          const isCompleted = isDone || segIdx < currentIdx
          const isCurrent = !isDone && segIdx === currentIdx

          return (
            <div key={code} className="flex flex-col items-center gap-[7px]">
              {isCompleted ? (
                <div className="w-[13px] h-[13px] rounded-full bg-stepdone" />
              ) : isCurrent ? (
                <div className="w-[15px] h-[15px] rounded-full bg-card border-4 border-abright" />
              ) : (
                <div className="w-[13px] h-[13px] rounded-full bg-card border-2 border-dotfuture" />
              )}
              <span
                className={[
                  'text-[9px] whitespace-nowrap',
                  isCurrent ? 'text-abright font-medium' : 'text-t3',
                ].join(' ')}
              >
                {label}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── §9.3 Quality progress counter ───────────────────────────────────────────

function QualityProgressBar({ qualityProgress, stalledSince, stageCode }) {
  if (!qualityProgress) return null

  const { label, current, target } = qualityProgress
  const isS2 = stageCode === 'S2'
  const isS3 = stageCode === 'S3'

  const displayLabel = isS2
    ? `已收集有效来源 ${current} / ${target} 条`
    : isS3
      ? `已完成证据引用 ${current} / ${target} 个`
      : `${label}：${current} / ${target}`

  const pct = target > 0 ? Math.min((current / target) * 100, 100) : 0

  const stalledMessage = stalledSince
    ? isS2
      ? '需要继续采集资料吗？可以粘贴链接或上传材料。'
      : isS3
        ? '需要进一步分析吗？可以让助手基于已有证据再拆一层。'
        : null
    : null

  return (
    <div className="mt-5">
      <div className="flex justify-between items-baseline mb-[6px]">
        <span className="text-12 text-t2">正文字数</span>
        <span className="text-12 text-text font-mono tabular-nums">{displayLabel}</span>
      </div>
      <div className="h-[6px] rounded-[3px] bg-track overflow-hidden">
        <div
          className="h-full rounded-[3px] bg-abright transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      {stalledMessage && (
        <p className="text-12 text-t3 italic mt-1">{stalledMessage}</p>
      )}
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function StagePanel({
  projectId,
  workspace,
  onRunIndependentReview,
  onExportDraft,
  onCheckpointSet,
  onInsertPrompt,
  reviewRunning = false,
}) {
  const summary = summarizeWorkspace(workspace)
  const {
    stageCode,
    stageLabel,
    statusLabel,
    completedItems,
    nextActions,
    qualityProgress,
    stalledSince,
    deliveryMode,
    lengthFallbackUsed,
    checkpoints,
  } = summary

  const stages = shouldShowPresentationStage(deliveryMode)
    ? REPORT_AND_PRESENTATION_STAGES
    : REPORT_ONLY_STAGES
  const totalStages = stages.length

  const stageNum = (() => {
    const idx = stages.findIndex(s => s.code === stageCode)
    return idx >= 0 ? idx + 1 : (stageCode === 'done' ? totalStages : '?')
  })()

  const independentReviewButton = getStageButtonState(
    'independent_review',
    stageCode,
    summary.flags,
    { reviewRunning },
  )
  const exportButton = getStageButtonState('export_draft', stageCode, summary.flags)

  const s5ToolButtonClass = (state) => [
    'flex-1 px-3 py-2 rounded-btn text-13 font-medium transition-colors disabled:opacity-40',
    state.highlighted
      ? 'btn-highlight-pulse bg-accent text-white hover:opacity-90'
      : 'border border-col bg-card2 text-text hover:bg-track',
  ].join(' ')

  return (
    <div className="flex-1 overflow-y-auto p-4 bg-ws">

      {/* Stage card */}
      <div className="bg-card border border-border rounded-card p-[15px]">

        {/* Header row */}
        <div className="flex justify-between items-start">
          <div>
            <div className="text-11 text-t3">
              当前阶段 · {stageNum} / {totalStages}
            </div>
            <div className="text-xl font-bold text-text mt-[3px] tracking-tight">
              {stageLabel}
            </div>
            <div className="text-12 text-t2 mt-[2px]">状态：{statusLabel}</div>
          </div>
          <RollbackMenu
            projectId={projectId}
            stageCode={stageCode}
            onCheckpointSet={onCheckpointSet}
            onInsertPrompt={onInsertPrompt}
          />
        </div>

        {/* Stepper */}
        <Stepper stageCode={stageCode} deliveryMode={deliveryMode} />

        {/* §9.3 Inline quality counter for S2/S3 */}
        {(stageCode === 'S2' || stageCode === 'S3') && (
          <QualityProgressBar
            qualityProgress={qualityProgress}
            stalledSince={stalledSince}
            stageCode={stageCode}
          />
        )}

        {/* §9.1/9.2 Stage advance buttons */}
        <StageAdvanceControl
          projectId={projectId}
          summary={summary}
          onCheckpointSet={onCheckpointSet}
          onInsertPrompt={onInsertPrompt}
          stageToolsRunning={reviewRunning}
        />

        {/* S5 tools and export button */}
        <div className="flex gap-2 mt-4">
          {independentReviewButton.visible && (
            <button
              type="button"
              onClick={onRunIndependentReview}
              disabled={independentReviewButton.disabled}
              className={s5ToolButtonClass(independentReviewButton)}
            >
              独立审查
            </button>
          )}
          {exportButton.visible && (
            <button
              type="button"
              onClick={onExportDraft}
              className="flex-1 px-3 py-2 rounded-btn border border-asoftb bg-asoft text-asoftt text-13 font-medium hover:opacity-90 transition-colors"
            >
              导出可审草稿
            </button>
          )}
        </div>
      </div>

      {/* Completed / next-actions checklist card */}
      <div className="bg-card border border-border rounded-card mt-[13px]">

        {/* Completed section */}
        {completedItems.length > 0 && (
          <div className="px-[15px] pt-[13px] pb-[3px]">
            <div className="text-11 font-bold tracking-[0.04em] text-success mb-[2px]">
              已完成
            </div>
            {completedItems.map((item, i) => (
              <div
                key={item}
                className={[
                  'flex gap-[10px] items-center py-2',
                  i > 0 ? 'border-t border-hair' : '',
                ].join(' ')}
              >
                <IconCheck size={14} className="text-success flex-shrink-0" />
                <span className="text-13 text-text">{item}</span>
              </div>
            ))}
          </div>
        )}

        {/* Divider between sections */}
        {completedItems.length > 0 && nextActions.length > 0 && (
          <div className="h-px bg-hair" />
        )}

        {/* Next actions section */}
        {nextActions.length > 0 && (
          <div className="px-[15px] pt-[13px] pb-[13px]">
            <div className="text-11 font-bold tracking-[0.04em] text-abright mb-[2px]">
              下一步
            </div>
            {nextActions.map((item, i) => (
              <div
                key={item}
                className={[
                  'flex gap-[10px] items-center py-2',
                  i > 0 ? 'border-t border-hair' : '',
                ].join(' ')}
              >
                <div className="w-[14px] h-[14px] rounded-full border-[1.6px] border-dotfuture flex-shrink-0" />
                <span className="text-13 text-t2">{item}</span>
              </div>
            ))}
          </div>
        )}

        {/* Empty state */}
        {completedItems.length === 0 && nextActions.length === 0 && (
          <div className="px-[15px] py-[13px]">
            <span className="text-13 text-t3">暂无进度信息</span>
          </div>
        )}
      </div>

    </div>
  )
}
