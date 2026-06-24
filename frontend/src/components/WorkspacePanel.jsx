import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import axios from 'axios'
import StagePanel from './StagePanel'
import FilePreviewPanel from './FilePreviewPanel'
import IndependentReviewDrawer from './IndependentReviewDrawer'
import { showError, showSuccess } from '../utils/toast'
import { shouldApplyProjectResponse } from '../utils/projectRequestOwnership'
import { getDefaultPreviewFile } from '../utils/workspaceFiles'
import { summarizeWorkspace } from '../utils/workspaceSummary'
import { DEFAULT_WORKSPACE_WIDTH } from '../utils/workspaceResize'

const WorkspacePanel = forwardRef(function WorkspacePanel({
  projectId,
  project,
  workspace,
  materials,
  refreshToken,
  onMaterialDeleted,
  onProjectMutated,
  onCheckpointSet,
  onInsertPrompt,
  onTriggerSystemTurn,
  onDropPendingReviewTriggers,
  width,
}, ref) {
  const [activeTab, setActiveTab] = useState('stage')
  const filePreviewRef = useRef(null)

  useImperativeHandle(ref, () => ({
    // App 切项目 / 收起面板前调用：把离开动作转交 FilePreviewPanel 的 attemptLeave
    //（allow 立即执行、dirty 弹三按钮后执行）。面板未挂载（非 files tab）则无编辑态，直接执行。
    attemptLeave: (action) => {
      const fp = filePreviewRef.current
      if (fp?.attemptLeave) return fp.attemptLeave(action)
      action?.()
      return true
    },
  }), [])

  const handleTabClick = useCallback((next) => {
    // 离开「文件」tab 是一条离开路径：经统一守卫（dirty 弹三按钮后再切）。
    if (activeTab === 'files' && next !== 'files' && filePreviewRef.current?.attemptLeave) {
      filePreviewRef.current.attemptLeave(() => setActiveTab(next))
      return
    }
    setActiveTab(next)
  }, [activeTab])
  const [files, setFiles] = useState([])
  const [currentFile, setCurrentFile] = useState('plan/project-overview.md')
  const [content, setContent] = useState('')
  const [reviewDrawerOpen, setReviewDrawerOpen] = useState(false)
  const [reviewRunning, setReviewRunning] = useState(false)
  const previousProjectRef = useRef(projectId)
  const activeProjectRef = useRef(projectId)
  // 最新文件请求标记：丢弃乱序返回的旧 GET，防它覆盖更新的预览内容（codex 前端 quality NIT）。
  const latestFileRequestRef = useRef(null)

  const loadFile = useCallback(async (path, requestProject = projectId) => {
    if (!requestProject || !path) return
    // 同步提交选择：currentFile 立即反映点击，消除「导航已发起、currentFile 还没异步 commit」的窗口——
    // 否则用户在内容 GET 返回前点「编辑」会锁定到错误文件（codex 前端 quality 审 BLOCKER）。内容仍异步加载。
    setCurrentFile(path)
    latestFileRequestRef.current = path
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/files/${path}`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      }) || latestFileRequestRef.current !== path) {
        return // 项目切了，或又点了别的文件——丢弃这个过期/乱序响应
      }
      setContent(res.data.content)
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      }) || latestFileRequestRef.current !== path) {
        return
      }
      setContent('文件不存在或无法读取')
    }
  }, [projectId])

  useEffect(() => {
    activeProjectRef.current = projectId
  }, [projectId])

  const loadFiles = useCallback(async () => {
    const requestProject = projectId
    if (!requestProject) return
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/files`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      // 结构化直传：{path, group, stage, editable, mtime_ns}；中文名/分组归 fileTree util
      setFiles(res.data.files)

      // BLOCKER 3：编辑态下只刷新上面的文件列表元数据，绝不重载当前文件 content
      //（否则覆盖编辑器底下的 preview，且 currentFile 变更会与编辑态 desync）。
      if (filePreviewRef.current?.isEditing?.()) {
        return
      }

      const paths = res.data.files.map(file => file.path)
      const nextDefault = paths.includes(currentFile)
        ? currentFile
        : getDefaultPreviewFile(paths)

      if (nextDefault) {
        await loadFile(nextDefault, requestProject)
      } else {
        setContent('')
      }
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      console.error('加载文件列表失败', error)
    }
  }, [projectId, currentFile, loadFile])

  useEffect(() => {
    if (projectId) {
      loadFiles()
    } else {
      setFiles([])
      setContent('')
    }
  }, [projectId, refreshToken, loadFiles])

  useEffect(() => {
    setReviewDrawerOpen(false)
    setReviewRunning(false)
    previousProjectRef.current = projectId
  }, [projectId])

  const runIndependentReview = () => {
    if (!projectId || reviewRunning) return
    // A new run supersedes any older pending independent_review_done for this project: drop it NOW,
    // before the new run's claim_first overwrites the store's done tombstone. Otherwise the stale
    // pending's later flush is run-bound-rejected and the older successful review surfaces as an
    // error (codex C5 red-team B2).
    onDropPendingReviewTriggers?.('independent_review_done')
    setReviewRunning(true)
    setReviewDrawerOpen(true)
  }

  const handleCloseReviewDrawer = useCallback(() => {
    setReviewDrawerOpen(false)
    setReviewRunning(false)
  }, [])

  const onIndependentReviewCompleted = useCallback((completion) => {
    // C5: completion is judged by the run-bound done tombstone the window reports
    // ({run_id, report_mtime_ns}) — NOT a generic workspace `independent_review_ready` flag
    // (which could reflect a stale/older report). The workspace refresh below only repaints UI.
    const requestProject = projectId
    if (!requestProject) {
      setReviewRunning(false)
      return
    }
    if (!shouldApplyProjectResponse({
      requestProject,
      activeProject: activeProjectRef.current,
    })) {
      setReviewRunning(false)
      return
    }
    onProjectMutated?.()
    const runId = completion?.run_id
    const reportMtimeNs = completion?.report_mtime_ns
    if (runId && reportMtimeNs) {
      // run_id / report_mtime_ns travel as opaque strings — passed through verbatim.
      onTriggerSystemTurn?.('independent_review_done', { run_id: runId, report_mtime_ns: reportMtimeNs })
    } else {
      showError('独立审查未返回有效结果，请重试')
    }
    setReviewRunning(false)
  }, [projectId, onProjectMutated, onTriggerSystemTurn])

  const handleSaveFile = useCallback(async (filePath, nextContent, baseMtimeNs) => {
    const requestProject = projectId
    if (!requestProject) return { ok: false, error: '无项目' }
    try {
      const res = await axios.post(
        `/api/projects/${encodeURIComponent(requestProject)}/files/${filePath}`,
        { content: nextContent, base_mtime_ns: baseMtimeNs },
      )
      if (shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        // R2 BLOCKER：成功后立即把预览 content 设为刚保存的内容——不能依赖 loadFiles 刷新，
        // 因为保存瞬间 FilePreviewPanel 仍在编辑态，loadFiles 的 isEditing early-return 会跳过 content 重载，
        // 导致回预览态后显示旧正文。
        setContent(nextContent)
        onProjectMutated?.() // 触发 workspace 刷新（review_stale 可能翻转）
      }
      return { ok: true, mtimeNs: res.data.mtime_ns }
    } catch (error) {
      if (error.response?.status === 409) return { ok: false, conflict: true }
      return { ok: false, error: error.response?.data?.detail || error.message }
    }
  }, [projectId, onProjectMutated])

  const reloadFile = useCallback(async (filePath) => {
    const requestProject = projectId
    const res = await axios.get(
      `/api/projects/${encodeURIComponent(requestProject)}/files/${filePath}`,
    )
    // NIT 3：点「编辑」后立刻切项目时，旧项目 GET 不得回填到新项目面板。
    if (!shouldApplyProjectResponse({
      requestProject,
      activeProject: activeProjectRef.current,
    })) {
      throw new Error('project switched') // FilePreviewPanel catch → 不进入编辑态
    }
    return { content: res.data.content, mtimeNs: res.data.mtime_ns }
  }, [projectId])

  const exportDraft = async () => {
    if (!projectId) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(projectId)}/export-draft`)
      if (res.data?.status !== 'ok') {
        showError('导出失败: ' + (res.data?.output || '未知错误'))
        return
      }
      // 触发浏览器下载（带 cookie 凭据；同源 anchor 即可）
      const a = document.createElement('a')
      a.href = `/api/projects/${encodeURIComponent(projectId)}/export-draft/download`
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
      showSuccess('已导出可审草稿，正在下载…')
      onProjectMutated?.()
    } catch (error) {
      showError('导出失败: ' + (error.response?.data?.detail || error.message))
    }
  }

  const deleteMaterial = async (materialId) => {
    if (!projectId) return
    try {
      await axios.delete(`/api/projects/${encodeURIComponent(projectId)}/materials/${encodeURIComponent(materialId)}`)
      onMaterialDeleted?.(materialId)
      onProjectMutated?.()
      showSuccess('材料已删除')
    } catch (error) {
      showError('删除材料失败: ' + (error.response?.data?.detail || error.message))
    }
  }

  const wsSummary = summarizeWorkspace(workspace)

  return (
    <div
      className="bg-[#1a1a2e] border-l border-[#2a2a4a] flex flex-col flex-shrink-0"
      style={{ width: width ?? DEFAULT_WORKSPACE_WIDTH }}
    >
      <div className="p-4 border-b border-[#2a2a4a]">
        <div className="flex gap-2">
          <button
            onClick={() => handleTabClick('stage')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'stage' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            阶段
          </button>
          <button
            onClick={() => handleTabClick('files')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'files' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            文件
          </button>
          <button
            onClick={() => handleTabClick('materials')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'materials' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            材料
          </button>
        </div>

        {/* §9.3 length_fallback hint — non-interactive; user adjusts length via chat */}
        {wsSummary.lengthFallbackUsed && (
          <div
            className="mt-2 w-full px-3 py-1.5 rounded-lg bg-[#2a1e10] border border-[#5a3a10] text-xs text-[#c8a060]"
            role="note"
          >
            预期字数：3000（默认值）
          </div>
        )}
      </div>

      {activeTab === 'stage' ? (
        <StagePanel
          projectId={projectId}
          workspace={workspace}
          onRunIndependentReview={runIndependentReview}
          onExportDraft={exportDraft}
          onCheckpointSet={onCheckpointSet}
          onInsertPrompt={onInsertPrompt}
          reviewRunning={reviewRunning}
        />
      ) : activeTab === 'files' ? (
        <FilePreviewPanel
          ref={filePreviewRef}
          files={files}
          currentFile={currentFile}
          content={content}
          currentStage={workspace?.stage_code}
          reviewStale={Boolean(workspace?.flags?.review_stale)}
          onSelectFile={loadFile}
          onSaveFile={handleSaveFile}
          onReloadFile={reloadFile}
        />
      ) : (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <div className="text-sm text-[#8f93c9]">
            {project?.workspace_dir || workspace?.workspace_dir || '未设置工作目录'}
          </div>
          {materials.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[#3a3a5a] p-4 text-sm text-[#8f93c9]">
              暂无项目材料。可以在聊天输入框左侧通过加号上传新材料。
            </div>
          ) : (
            materials.map(material => (
              <div key={material.id} className="rounded-lg border border-[#2f3158] bg-[#15162d] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm text-[#e2e2f0] break-all">{material.display_name}</div>
                    <div className="mt-1 text-xs text-[#8f93c9]">
                      {material.source_type} · {material.file_type || '未知类型'}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => deleteMaterial(material.id)}
                    className="text-xs text-red-300 hover:text-red-200"
                  >
                    删除
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}
      <IndependentReviewDrawer
        projectId={projectId}
        isOpen={reviewDrawerOpen}
        onClose={handleCloseReviewDrawer}
        onCompleted={onIndependentReviewCompleted}
      />
    </div>
  )
})

export default WorkspacePanel
