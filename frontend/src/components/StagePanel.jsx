import React from 'react'
import { summarizeWorkspace } from '../utils/workspaceSummary'

export default function StagePanel({ workspace, qualityResult, onRunQualityCheck, onExportDraft }) {
  const summary = summarizeWorkspace(workspace)

  return (
    <div className="flex-1 overflow-y-auto p-5 bg-[#101224]">
      <div className="rounded-2xl border border-[#2f3158] bg-[#171a31] p-5 mb-4">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <div className="text-xs uppercase tracking-[0.24em] text-[#64ffda] mb-2">Workspace</div>
            <h3 className="text-xl font-semibold text-[#eef1ff]">当前阶段 {summary.stageLabel}</h3>
            <p className="text-sm text-[#8f93c9] mt-1">状态：{summary.statusLabel}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={onRunQualityCheck} className="px-3 py-2 rounded-lg bg-[#26315d] text-[#eef1ff] text-sm hover:bg-[#32407a]">
              运行质量检查
            </button>
            <button onClick={onExportDraft} className="px-3 py-2 rounded-lg bg-blue-600 text-white text-sm hover:bg-blue-700">
              导出可审草稿
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl bg-[#111428] border border-[#272a4f] p-4">
            <div className="text-sm font-medium text-[#64ffda] mb-3">已完成</div>
            <div className="space-y-2">
              {summary.completedItems.length > 0 ? summary.completedItems.map(item => (
                <div key={item} className="text-sm text-[#d9dcf5]">[x] {item}</div>
              )) : <div className="text-sm text-[#8f93c9]">还没有已完成项</div>}
            </div>
          </div>
          <div className="rounded-xl bg-[#111428] border border-[#272a4f] p-4">
            <div className="text-sm font-medium text-[#64ffda] mb-3">下一步</div>
            <div className="space-y-2">
              {summary.nextActions.length > 0 ? summary.nextActions.map(item => (
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
