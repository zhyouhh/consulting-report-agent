import React, { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import StagePanel from './StagePanel'
import FilePreviewPanel from './FilePreviewPanel'
import { showError, showSuccess } from '../utils/toast'
import { getNextQualityResult } from '../utils/workspacePanelState'
import { shouldApplyProjectResponse } from '../utils/projectRequestOwnership'

export default function WorkspacePanel({ project, workspace, refreshToken, onProjectMutated }) {
  const [activeTab, setActiveTab] = useState('stage')
  const [files, setFiles] = useState([])
  const [currentFile, setCurrentFile] = useState('plan/project-overview.md')
  const [content, setContent] = useState('')
  const [qualityResult, setQualityResult] = useState(null)
  const previousProjectRef = useRef(project)
  const activeProjectRef = useRef(project)

  const loadFile = useCallback(async (path, requestProject = project) => {
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
  }, [project])

  useEffect(() => {
    activeProjectRef.current = project
  }, [project])

  const loadFiles = useCallback(async () => {
    const requestProject = project
    if (!requestProject) return
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/files`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      const fileList = res.data.files.map(path => ({
        name: path.split('/').pop().replace('.md', ''),
        path,
      }))
      setFiles(fileList)

      const nextDefault = fileList.find(file => file.path === currentFile)?.path
        || fileList.find(file => file.path === 'plan/project-overview.md')?.path
        || fileList[0]?.path

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
  }, [project, currentFile, loadFile])

  useEffect(() => {
    if (project) {
      loadFiles()
    } else {
      setFiles([])
      setContent('')
    }
  }, [project, refreshToken, loadFiles])

  useEffect(() => {
    setQualityResult(currentResult => getNextQualityResult({
      currentResult,
      previousProject: previousProjectRef.current,
      nextProject: project,
    }))
    previousProjectRef.current = project
  }, [project])

  const runQualityCheck = async () => {
    if (!project) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(project)}/quality-check`)
      setQualityResult(res.data)
      onProjectMutated?.()
    } catch (error) {
      showError('质量检查失败: ' + (error.response?.data?.detail || error.message))
    }
  }

  const exportDraft = async () => {
    if (!project) return
    try {
      const res = await axios.post(`/api/projects/${encodeURIComponent(project)}/export-draft`)
      showSuccess(`已导出可审草稿：${res.data.output_path}`)
      onProjectMutated?.()
    } catch (error) {
      showError('导出失败: ' + (error.response?.data?.detail || error.message))
    }
  }

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
        </div>
      </div>

      {activeTab === 'stage' ? (
        <StagePanel
          workspace={workspace}
          qualityResult={qualityResult}
          onRunQualityCheck={runQualityCheck}
          onExportDraft={exportDraft}
        />
      ) : (
        <FilePreviewPanel
          files={files}
          currentFile={currentFile}
          content={content}
          onSelectFile={loadFile}
        />
      )}
    </div>
  )
}
