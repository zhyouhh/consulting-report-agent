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
import { IconFile, IconTrash, IconUpload } from './icons'

const WorkspacePanel = forwardRef(function WorkspacePanel({
  projectId,
  project,
  workspace,
  materials,
  refreshToken,
  onMaterialsMerged,
  onMaterialDeleted,
  onProjectMutated,
  onCheckpointSet,
  onInsertPrompt,
  onTriggerSystemTurn,
  onDropPendingReviewTriggers,
  width,
}, ref) {
  const [activeTab, setActiveTab] = useState('stage')
  const [materialUploading, setMaterialUploading] = useState(false)
  const filePreviewRef = useRef(null)
  const uploadInputRef = useRef(null)

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
  // 渲染期同步更新（与 ChatPanel 一致）：被动 useEffect 会在 commit 后才赋值，留下「UI 已切到 B、
  // ref 仍是 A」的窗口，late 上传完成可能 stillActive 通过、把 A 的结果并进 B（codex 红队 BLOCKER）。
  // 渲染期赋值消除切项目窗口；unmount 场景另由 mountedRef 兜底。
  activeProjectRef.current = projectId
  // 挂载守卫：面板隐藏（unmount）后渲染不再发生、activeProjectRef 冻在旧项目，render 期赋值救不了
  // 「上传中途收起面板 + 切项目」；mountedRef 在 unmount 置 false，配合 stillActive() → unmount 后
  // 一律不再回调父级 / 弹提示。
  const mountedRef = useRef(true)
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

  useEffect(() => () => { mountedRef.current = false }, [])

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

  // 材料 tab 直接上传到项目材料库（复用聊天回形针同一个 /materials/upload 端点）。
  // 与聊天「待发送附件」不同：这里上传即入库、立即出现在「已上传材料」列表。
  const uploadMaterialFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList || [])
    if (!files.length || !projectId || materialUploading) return
    const requestProject = projectId
    setMaterialUploading(true)
    // 上传途中可能切了项目：结果与提示都只对仍激活的项目生效，避免把旧项目材料并进新项目列表
    // （成功路径），也避免在新项目界面弹旧项目的失败提示（失败路径，codex 红队 BLOCKER）。
    const stillActive = () => mountedRef.current && shouldApplyProjectResponse({
      requestProject,
      activeProject: activeProjectRef.current,
    })
    try {
      const formData = new FormData()
      files.forEach(file => formData.append('files', file))
      const res = await axios.post(
        `/api/projects/${encodeURIComponent(requestProject)}/materials/upload`,
        formData,
      )
      if (!stillActive()) return
      const uploaded = res.data.materials || []
      if (uploaded.length > 0) {
        onMaterialsMerged?.(uploaded)
        onProjectMutated?.()
        showSuccess(`已上传 ${uploaded.length} 份材料`)
      } else {
        showError('未能上传任何材料')
      }
    } catch (error) {
      if (!stillActive()) return
      showError('上传材料失败: ' + (error.response?.data?.detail || error.message))
    } finally {
      setMaterialUploading(false)
    }
  }, [projectId, materialUploading, onMaterialsMerged, onProjectMutated])

  const handleSelectUploadFiles = (event) => {
    uploadMaterialFiles(event.target.files)
    event.target.value = '' // 允许连选同一文件再次触发 change
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
      className="bg-ws flex flex-col flex-shrink-0 min-w-0"
      style={{ width: width ?? DEFAULT_WORKSPACE_WIDTH }}
    >
      {/* tabs 段控区 */}
      <div className="px-4 pt-[14px] pb-3">
        <div className="flex bg-track rounded-btn p-[2px]">
          {[['stage', '阶段'], ['files', '文件'], ['materials', '材料']].map(([key, label]) => (
            <button
              key={key}
              onClick={() => handleTabClick(key)}
              className={`flex-1 text-center text-13 py-[5px] rounded-tag cursor-pointer transition-colors ${
                activeTab === key
                  ? 'bg-card shadow-card text-text font-medium'
                  : 'text-t2 font-normal'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* §9.3 length_fallback hint — non-interactive; user adjusts length via chat */}
        {wsSummary.lengthFallbackUsed && (
          <div
            className="mt-2 w-full px-3 py-1.5 rounded-btn bg-asoft border border-col text-12 text-asoftt"
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
        <div className="flex-1 overflow-y-auto p-4">
          {/* 顶部：标题 + 上传按钮 */}
          <div className="flex items-center justify-between mb-3">
            <span className="text-12 text-t2">已上传材料 · {materials.length}</span>
            <input
              ref={uploadInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={handleSelectUploadFiles}
            />
            <button
              type="button"
              className="flex items-center gap-[6px] px-[11px] py-[5px] rounded-ibtn border border-border bg-card2 text-text text-12 hover:bg-card2/70 disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={() => uploadInputRef.current?.click()}
              disabled={!projectId || materialUploading}
              title="上传项目材料"
            >
              <IconUpload size={13} />
              {materialUploading ? '上传中…' : '上传'}
            </button>
          </div>

          {/* 工作目录展示（功能全保：原版材料 tab 顶部即显示项目工作目录） */}
          <div className="text-11 text-t3 font-mono break-all mb-3">
            {project?.workspace_dir || workspace?.workspace_dir || '未设置工作目录'}
          </div>

          {materials.length === 0 ? (
            <div className="rounded-card border border-border border-dashed p-4 text-13 text-t2">
              暂无项目材料。点击右上角「上传」按钮，或在聊天输入框左侧的回形针添加材料。
            </div>
          ) : (
            materials.map(material => (
              <div key={material.id} className="flex items-center gap-[11px] px-[13px] py-[11px] bg-card border border-border rounded-[10px] mb-2">
                {/* 图标 */}
                <div className="w-[30px] h-[30px] rounded-ibtn bg-asoft text-asoftt flex items-center justify-center flex-shrink-0">
                  <IconFile size={15} />
                </div>
                {/* 文件信息 */}
                <div className="min-w-0 flex-1">
                  <div className="text-13 font-medium text-text truncate">{material.display_name}</div>
                  <div className="text-11 text-t3 mt-[2px]">
                    {material.source_type} · {material.file_type || '未知类型'}
                  </div>
                </div>
                {/* 删除按钮 */}
                <button
                  type="button"
                  onClick={() => deleteMaterial(material.id)}
                  title={`删除材料：${material.display_name}`}
                  aria-label={`删除材料：${material.display_name}`}
                  className="w-[26px] h-[26px] rounded-md text-t3 hover:bg-card2 hover:text-error flex items-center justify-center flex-shrink-0"
                >
                  <IconTrash size={14} />
                </button>
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
