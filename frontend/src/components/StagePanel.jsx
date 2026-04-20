import React from 'react'
import { summarizeWorkspace, shouldShowPresentationStage, getStageName } from '../utils/workspaceSummary'
import StageAdvanceControl from './StageAdvanceControl'
import RollbackMenu from './RollbackMenu'

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

function ProgressBar({ stageCode, deliveryMode, checkpoints }) {
  const stages = shouldShowPresentationStage(deliveryMode)
    ? REPORT_AND_PRESENTATION_STAGES
    : REPORT_ONLY_STAGES

  const currentIdx = getStageIndex(stageCode)
  const isDone = stageCode === 'done'

  return (
    <div className="flex gap-0.5 mt-3">
      {stages.map(({ code, label }) => {
        const segIdx = getStageIndex(code)
        const isCompleted = isDone || segIdx < currentIdx
        const isCurrent = !isDone && segIdx === currentIdx

        const bgColor = isCompleted
          ? 'bg-[#4a5fcc]'
          : isCurrent
            ? 'bg-[#3b4fa8]'
            : 'bg-[#1e2140]'

        const borderColor = isCurrent ? 'border-[#6070e0]' : 'border-transparent'

        return (
          <div
            key={code}
            className="flex-1 group relative"
            title={`${label}${isCompleted ? ' ✓' : isCurrent ? ' （当前）' : ''}`}
          >
            <div className={`h-1.5 rounded-full border ${bgColor} ${borderColor} transition-all`} />
            {/* Hover tooltip */}
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-30 pointer-events-none">
              <div className="bg-[#0e1020] border border-[#2f3158] rounded-lg px-2.5 py-1.5 text-xs text-[#c8ccee] whitespace-nowrap shadow-lg">
                {label}
                {isCompleted && <span className="ml-1 text-[#64ffda]">✓</span>}
                {isCurrent && <span className="ml-1 text-[#a8b0ff]">当前</span>}
              </div>
            </div>
          </div>
        )
      })}
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
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs text-[#8f93c9]">{displayLabel}</span>
        <span className="text-xs text-[#5a5e80]">{Math.round(pct)}%</span>
      </div>
      <div className="h-1 rounded-full bg-[#1e2140] overflow-hidden">
        <div
          className="h-full rounded-full bg-[#4a5fcc] transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      {stalledMessage && (
        <p className="text-xs text-[#5a5e80] italic mt-1">{stalledMessage}</p>
      )}
    </div>
  )
}

// ── Main component ───────────────────────────────────────────────────────────

export default function StagePanel({
  projectId,
  workspace,
  qualityResult,
  onRunQualityCheck,
  onExportDraft,
  onCheckpointSet,
  onInsertPrompt,
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

  return (
    <div className="flex-1 overflow-y-auto p-5 bg-[#101224]">
      <div className="rounded-2xl border border-[#2f3158] bg-[#171a31] p-5 mb-4">

        {/* Header row: stage label + rollback menu */}
        <div className="flex items-start justify-between gap-3 mb-1">
          <div>
            <div className="text-xs uppercase tracking-[0.24em] text-[#64ffda] mb-2">Workspace</div>
            <h3 className="text-xl font-semibold text-[#eef1ff]">当前阶段 {stageLabel}</h3>
            <p className="text-sm text-[#8f93c9] mt-1">状态：{statusLabel}</p>
          </div>
          <RollbackMenu
            projectId={projectId}
            stageCode={stageCode}
            onCheckpointSet={onCheckpointSet}
            onInsertPrompt={onInsertPrompt}
          />
        </div>

        {/* §9.6 Progress bar */}
        <ProgressBar
          stageCode={stageCode}
          deliveryMode={deliveryMode}
          checkpoints={checkpoints}
        />

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
        />

        {/* Export button */}
        <div className="flex gap-2 mt-4">
          <button
            onClick={onRunQualityCheck}
            className="flex-1 px-3 py-2 rounded-lg bg-[#26315d] text-[#eef1ff] text-sm hover:bg-[#32407a] transition-colors"
          >
            运行质量检查
          </button>
          <button
            onClick={onExportDraft}
            className="flex-1 px-3 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700 transition-colors"
          >
            导出可审草稿
          </button>
        </div>
      </div>

      {/* Completed / next-actions grid */}
      <div className="rounded-2xl border border-[#2f3158] bg-[#171a31] p-5 mb-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl bg-[#111428] border border-[#272a4f] p-4">
            <div className="text-sm font-medium text-[#64ffda] mb-3">已完成</div>
            <div className="space-y-2">
              {completedItems.length > 0 ? completedItems.map(item => (
                <div key={item} className="text-sm text-[#d9dcf5]">[x] {item}</div>
              )) : <div className="text-sm text-[#8f93c9]">还没有已完成项</div>}
            </div>
          </div>
          <div className="rounded-xl bg-[#111428] border border-[#272a4f] p-4">
            <div className="text-sm font-medium text-[#64ffda] mb-3">下一步</div>
            <div className="space-y-2">
              {nextActions.length > 0 ? nextActions.map(item => (
                <div key={item} className="text-sm text-[#d9dcf5]">[ ] {item}</div>
              )) : <div className="text-sm text-[#8f93c9]">暂无下一步建议</div>}
            </div>
          </div>
        </div>
      </div>

      {qualityResult && (
        <div className="rounded-2xl border border-[#2f3158] bg-[#171a31] p-5">
          <div className="text-sm font-medium text-[#64ffda] mb-3">质检结果</div>
          <div className={`inline-flex px-2 py-1 rounded text-xs mb-3 ${
            qualityResult.status === 'ok' ? 'bg-[#173a2d] text-[#8ef0c3]' : 'bg-[#4a2121] text-[#ffb6b6]'
          }`}>
            {qualityResult.status === 'ok' ? '检查完成' : '检查失败'}
          </div>
          <pre className="whitespace-pre-wrap text-sm leading-6 text-[#d9dcf5] bg-[#0e1020] border border-[#252846] rounded-xl p-4 overflow-x-auto">
            {qualityResult.output || '暂无输出'}
          </pre>
        </div>
      )}
    </div>
  )
}
