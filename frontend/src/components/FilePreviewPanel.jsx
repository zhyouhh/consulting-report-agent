import React, {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import 'highlight.js/styles/github-dark.css'
import 'katex/dist/katex.min.css'
import { buildFileTree } from '../utils/fileTree'
import {
  initialEditState, enterEdit, editDraft, startSaving,
  saveSucceeded, saveFailed, reloadAfterConflict, guardLeave,
} from '../utils/fileEditState'
import { showError } from '../utils/toast'

const markdownComponents = {
  code: ({ inline, className, children, ...props }) => (
    inline ? (
      <code className="px-1.5 py-0.5 bg-[#1a1a2e] text-[#64ffda] rounded text-sm font-mono" {...props}>
        {children}
      </code>
    ) : (
      <code className={className} {...props}>{children}</code>
    )
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto my-4">
      <table className="min-w-full border-collapse border border-[#2a2a4a]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-[#2a2a4a] bg-[#1a1a2e] px-4 py-2 text-left text-[#64ffda] font-semibold">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border border-[#2a2a4a] px-4 py-2 text-[#e2e2f0]">{children}</td>
  ),
  img: ({ src, alt }) => (
    <img src={src} alt={alt} className="max-w-full h-auto rounded-lg shadow-lg my-4" />
  ),
  a: ({ href, children }) => (
    <a href={href} className="text-[#64ffda] hover:text-[#52e0c2] underline" target="_blank" rel="noopener noreferrer">{children}</a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-[#64ffda] pl-4 py-2 my-4 bg-[#1a1a2e] text-[#c8c8e0] italic">{children}</blockquote>
  ),
  h1: ({ children }) => <h1 className="text-3xl font-bold text-[#e2e2f0] mt-6 mb-4 pb-2 border-b border-[#2a2a4a]">{children}</h1>,
  h2: ({ children }) => <h2 className="text-2xl font-bold text-[#e2e2f0] mt-5 mb-3">{children}</h2>,
  h3: ({ children }) => <h3 className="text-xl font-semibold text-[#e2e2f0] mt-4 mb-2">{children}</h3>,
  p: ({ children }) => <p className="text-[#c8c8e0] leading-7 mb-4">{children}</p>,
  ul: ({ children }) => <ul className="list-disc list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside text-[#c8c8e0] mb-4 space-y-2">{children}</ol>,
}

const DRAFT_PATH = 'content/report_draft_v1.md'

const FilePreviewPanel = forwardRef(function FilePreviewPanel({
  files = [],
  currentFile,
  content,
  currentStage = null,
  reviewStale = false,
  onSelectFile,
  onSaveFile,
  onReloadFile,
}, ref) {
  const [edit, setEdit] = useState(initialEditState)
  const [collapsed, setCollapsed] = useState({})
  const [leaveDialog, setLeaveDialog] = useState(null) // null | { action }
  const editRef = useRef(edit)
  editRef.current = edit

  const currentEditable = useMemo(
    () => Boolean(files.find((f) => f.path === currentFile)?.editable),
    [files, currentFile],
  )
  const groups = useMemo(() => buildFileTree(files, currentStage), [files, currentStage])
  const inEdit = edit.mode === 'edit'
  const isDraft = currentFile === DRAFT_PATH

  // 统一离开守卫（延后动作模式）：调用方传入「离开动作」action。
  //   allow（预览 / 未改）→ 重置编辑态 + 立即执行 action，返回 true
  //   block（保存中）     → 提示，返回 false（不离开）
  //   confirm（有未保存改动）→ 弹三按钮对话框、把 action 挂起，返回 false；
  //                          用户点「保存」/「放弃修改」后再执行 action（见 handleLeaveSave/Discard）
  const attemptLeave = useCallback((action) => {
    const decision = guardLeave(editRef.current)
    if (decision === 'allow') {
      setEdit(initialEditState())
      action?.()
      return true
    }
    if (decision === 'block') {
      showError('正在保存，请稍候')
      return false
    }
    setLeaveDialog({ action })
    return false
  }, [])

  useImperativeHandle(ref, () => ({
    // WorkspacePanel/App 切 tab / 切项目 / 收起面板前调用：传入离开动作；
    // allow 立即执行、dirty 弹三按钮后再执行。返回是否已同步放行。
    attemptLeave: (action) => attemptLeave(action),
    // WorkspacePanel.loadFiles 用：编辑态下 refreshToken 刷新只更新文件列表元数据、不重载当前文件 content。
    isEditing: () => editRef.current.mode === 'edit',
  }), [attemptLeave])

  // best-effort：整页刷新 / PyWebView 关窗时，dirty 或 saving 则拦截。
  useEffect(() => {
    const onBeforeUnload = (e) => {
      if (guardLeave(editRef.current) !== 'allow') {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  // 实际保存：POST 当前草稿，按结果更新编辑态，返回 result 供工具栏 / 离开弹窗分别处理。
  const doSave = useCallback(async () => {
    const snapshot = editRef.current
    if (snapshot.mode !== 'edit' || snapshot.saving) return { ok: false }
    setEdit((prev) => startSaving(prev))
    const result = await onSaveFile(currentFile, snapshot.draft, snapshot.baseMtimeNs)
    if (result?.ok) {
      setEdit((prev) => saveSucceeded(prev, { mtimeNs: result.mtimeNs }))
    } else {
      setEdit((prev) => saveFailed(prev))
    }
    return result || { ok: false }
  }, [currentFile, onSaveFile])

  // 工具栏「保存」：存成功留在当前文件；撞 409 给「重新加载 / 不动」二选一（reload 决策，非离开决策）。
  const handleSave = useCallback(async () => {
    const result = await doSave()
    if (result?.ok) return
    if (result?.conflict) {
      const reload = window.confirm('文件已被更新（可能是 AI 刚写过），加载最新内容？本地修改将丢弃。')
      if (reload) {
        try {
          const fresh = await onReloadFile(currentFile)
          setEdit((prev) => reloadAfterConflict(prev, { content: fresh.content, mtimeNs: fresh.mtimeNs }))
        } catch (error) {
          showError('重新加载失败：' + (error?.message || ''))
        }
      }
      return
    }
    showError('保存失败：' + (result?.error || '请重试'))
  }, [doSave, currentFile, onReloadFile])

  const handleEnterEdit = useCallback(async () => {
    try {
      const fresh = await onReloadFile(currentFile) // 重新取最新 {content, mtimeNs} 作 base
      setEdit((prev) => enterEdit(prev, { content: fresh.content, mtimeNs: fresh.mtimeNs }))
    } catch (error) {
      showError('无法进入编辑：' + (error?.message || '读取失败'))
    }
  }, [currentFile, onReloadFile])

  // 切换预览文件本身是一条离开路径：经统一守卫（dirty 弹三按钮）。
  const handleSelectFile = useCallback((path) => {
    if (path === currentFile) return
    attemptLeave(() => onSelectFile?.(path))
  }, [currentFile, attemptLeave, onSelectFile])

  // 工具栏「取消」：放弃编辑回预览；dirty 时也经守卫确认。
  const handleCancel = useCallback(() => {
    attemptLeave(() => {})
  }, [attemptLeave])

  // 离开弹窗「保存」：存成功后再执行挂起的离开动作；失败（含 409）则不离开、留在编辑态。
  const handleLeaveSave = useCallback(async () => {
    const dialog = leaveDialog
    if (!dialog) return
    const result = await doSave()
    if (result?.ok) {
      setLeaveDialog(null)
      dialog.action?.()
      return
    }
    setLeaveDialog(null)
    if (result?.conflict) {
      showError('文件已更新（可能是 AI 刚写过），请重新加载后再编辑')
    } else {
      showError('保存失败：' + (result?.error || '请重试'))
    }
  }, [leaveDialog, doSave])

  // 离开弹窗「放弃修改」：弃改后执行挂起的离开动作。
  const handleLeaveDiscard = useCallback(() => {
    const dialog = leaveDialog
    setLeaveDialog(null)
    setEdit(initialEditState())
    dialog?.action?.()
  }, [leaveDialog])

  // 离开弹窗「取消」：关窗、留在当前继续编辑。
  const closeLeaveDialog = useCallback(() => setLeaveDialog(null), [])

  return (
    <div className="flex-1 flex flex-col min-h-0 relative">
      {/* 文件树（分组 + 当前阶段置顶 + 中文名） */}
      <div className="border-b border-[#2a2a4a] max-h-64 overflow-y-auto text-sm">
        {groups.map((group) => {
          const isCollapsed = collapsed[group.group] ?? group.defaultCollapsed
          return (
            <div key={group.group}>
              <div
                onClick={() => setCollapsed((c) => ({ ...c, [group.group]: !isCollapsed }))}
                className="px-3 py-1.5 cursor-pointer text-xs text-[#8f93c9] tracking-wide hover:bg-[#1c1c38] flex items-center justify-between"
              >
                <span>{group.label}</span>
                <span>{isCollapsed ? '▸' : '▾'}</span>
              </div>
              {!isCollapsed && group.files.map((file) => (
                <div
                  key={file.path}
                  onClick={() => handleSelectFile(file.path)}
                  className={`px-4 py-2 cursor-pointer text-sm flex items-center gap-2 ${
                    currentFile === file.path ? 'bg-[#1e1e4a] text-blue-400' : 'hover:bg-[#222244] text-[#c8c8e0]'
                  } ${file.isCurrentStage ? 'border-l-2 border-[#64ffda]' : 'border-l-2 border-transparent'}`}
                >
                  <span className="truncate flex-1">{file.label}</span>
                  {file.isCurrentStage && <span className="text-[10px] text-[#64ffda]">当前</span>}
                  {!file.editable && <span className="text-[10px] text-[#6a6a8a]">只读</span>}
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {/* review_stale advisory（仅正文页显示） */}
      {isDraft && reviewStale && (
        <div className="px-4 py-2 text-xs text-[#c8a060] bg-[#2a1e10] border-b border-[#5a3a10]" role="note">
          正文已改动，建议重新审查（独立审查 / AI 味自查报告可能已过期）。
        </div>
      )}

      {/* 工具栏 */}
      <div className="px-4 py-2 border-b border-[#2a2a4a] flex items-center gap-2 min-h-[2.75rem]">
        {!inEdit && currentEditable && (
          <button onClick={handleEnterEdit} className="px-3 py-1 rounded text-xs bg-[#28366b] text-white">编辑</button>
        )}
        {inEdit && (
          <>
            <button onClick={handleSave} disabled={edit.saving} className="px-3 py-1 rounded text-xs bg-[#2f7d52] text-white disabled:opacity-50">
              {edit.saving ? '保存中…' : '保存'}
            </button>
            <button onClick={handleCancel} disabled={edit.saving} className="px-3 py-1 rounded text-xs bg-[#15162d] text-[#8f93c9] disabled:opacity-50">
              取消
            </button>
          </>
        )}
      </div>

      {/* 正文区：编辑态 textarea / 预览态 markdown */}
      <div className="flex-1 overflow-y-auto p-6 bg-[#0d0d1a]">
        {inEdit ? (
          <textarea
            value={edit.draft}
            onChange={(e) => setEdit((prev) => editDraft(prev, e.target.value))}
            className="w-full h-full min-h-[20rem] bg-[#0d0d1a] text-[#e2e2f0] font-mono text-sm leading-6 outline-none resize-none"
            spellCheck={false}
          />
        ) : (
          <div className="markdown-body max-w-none selectable-content">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex, rehypeHighlight, rehypeRaw]}
              components={markdownComponents}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>

      {/* 脏离开三按钮对话框（保存 / 放弃修改 / 取消）——延后动作模式（spec §7.2 v1）。
          beforeunload（整页刷新/关窗）受原生限制仍走二选一，无法升级为三按钮。 */}
      {leaveDialog && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/50" role="dialog" aria-modal="true" aria-label="未保存的修改">
          <div className="w-80 rounded-lg border border-[#2a2a4a] bg-[#1a1a2e] p-5 shadow-xl">
            <div className="mb-4 text-sm text-[#e2e2f0]">当前文件有未保存的修改，如何处理？</div>
            <div className="flex flex-col gap-2">
              <button onClick={handleLeaveSave} disabled={edit.saving} className="px-3 py-2 rounded text-sm bg-[#2f7d52] text-white disabled:opacity-50">
                {edit.saving ? '保存中…' : '保存'}
              </button>
              <button onClick={handleLeaveDiscard} disabled={edit.saving} className="px-3 py-2 rounded text-sm bg-[#5a2a2a] text-[#f0c8c8] disabled:opacity-50">
                放弃修改
              </button>
              <button onClick={closeLeaveDialog} disabled={edit.saving} className="px-3 py-2 rounded text-sm bg-[#15162d] text-[#8f93c9] disabled:opacity-50">
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
})

export default FilePreviewPanel
