import React, { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import StagePanel from './StagePanel'
import FilePreviewPanel from './FilePreviewPanel'
import { showError, showSuccess } from '../utils/toast'
import { getNextQualityResult } from '../utils/workspacePanelState'
import { shouldApplyProjectResponse } from '../utils/projectRequestOwnership'
import { getDefaultPreviewFile, orderPreviewFiles } from '../utils/workspaceFiles'
import { summarizeWorkspace } from '../utils/workspaceSummary'

export default function WorkspacePanel({
  projectId,
  project,
  workspace,
  materials,
  refreshToken,
  onMaterialDeleted,
  onProjectMutated,
  onCheckpointSet,
  onInsertPrompt,
  onOpenEditProject,
}) {
  const [activeTab, setActiveTab] = useState('stage')
  const [files, setFiles] = useState([])
  const [currentFile, setCurrentFile] = useState('plan/project-overview.md')
  const [content, setContent] = useState('')
  const [qualityResult, setQualityResult] = useState(null)
  const previousProjectRef = useRef(projectId)
  const activeProjectRef = useRef(projectId)

  const loadFile = useCallback(async (path, requestProject = projectId) => {
    if (!requestProject || !path) return
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/files/${path}`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      setContent(res.data.content)
      setCurrentFile(path)
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
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
      const orderedPaths = orderPreviewFiles(res.data.files)
      const fileList = orderedPaths.map(path => ({
        name: path.split('/').pop().replace('.md', ''),
        path,
      }))
      setFiles(fileList)

      const nextDefault = fileList.find(file => file.path === currentFile)?.path
        || getDefaultPreviewFile(orderedPaths)

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
    setQualityResult(currentResult => getNextQualityResult({
      currentResult,
      previousProject: previousProjectRef.current,
      nextProject: projectId,
    }))
    previousProjectRef.current = projectId
  }, [projectId])

  const runQualityCheck = async () => {
    if (!projectId) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(projectId)}/quality-check`)
      setQualityResult(res.data)
      onProjectMutated?.()
    } catch (error) {
      showError('质量检查失败: ' + (error.response?.data?.detail || error.message))
    }
  }

  const exportDraft = async () => {
    if (!projectId) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(projectId)}/export-draft`)
      showSuccess(`已导出可审草稿：${res.data.output_path}`)
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
    <div className="w-[28rem] bg-[#1a1a2e] border-l border-[#2a2a4a] flex flex-col">
      <div className="p-4 border-b border-[#2a2a4a]">
        <div className="flex gap-2">
          <button
            onClick={() => setActiveTab('stage')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'stage' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            阶段
          </button>
          <button
            onClick={() => setActiveTab('files')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'files' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            文件
          </button>
          <button
            onClick={() => setActiveTab('materials')}
            className={`px-3 py-2 rounded-lg text-sm ${activeTab === 'materials' ? 'bg-[#28366b] text-white' : 'bg-[#15162d] text-[#8f93c9]'}`}
          >
            材料
          </button>
        </div>

        {/* §9.3 length_fallback chip — shown when backend used default word target */}
        {wsSummary.lengthFallbackUsed && (
          <button
            onClick={onOpenEditProject}
            className="mt-2 w-full text-left px-3 py-1.5 rounded-lg bg-[#2a1e10] border border-[#5a3a10] text-xs text-[#c8a060] hover:bg-[#3a2810] transition-colors"
          >
            预期字数：3000（默认值，点击修改）
          </button>
        )}
      </div>

      {activeTab === 'stage' ? (
        <StagePanel
          projectId={projectId}
          workspace={workspace}
          qualityResult={qualityResult}
          onRunQualityCheck={runQualityCheck}
          onExportDraft={exportDraft}
          onCheckpointSet={onCheckpointSet}
          onInsertPrompt={onInsertPrompt}
          onOpenEditProject={onOpenEditProject}
        />
      ) : activeTab === 'files' ? (
        <FilePreviewPanel
          files={files}
          currentFile={currentFile}
          content={content}
          onSelectFile={loadFile}
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
    </div>
  )
}
