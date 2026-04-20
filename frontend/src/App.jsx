import React, { useState, useEffect, useRef } from 'react'
import { Toaster } from 'react-hot-toast'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import WorkspacePanel from './components/WorkspacePanel'
import axios from 'axios'
import { shouldApplyProjectResponse } from './utils/projectRequestOwnership'
import { mergeMaterials, removeMaterialById } from './utils/chatMaterials'
import { getCurrentProject, isSameProjectSelection, reconcileCurrentProjectId } from './utils/projectSelection'

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  render() {
    if (this.state.hasError) {
      return <div className="flex items-center justify-center h-screen"><div className="text-red-600">应用出错，请刷新页面</div></div>
    }
    return this.props.children
  }
}

function App() {
  const [projects, setProjects] = useState([])
  const [currentProjectId, setCurrentProjectId] = useState(null)
  const [currentProject, setCurrentProject] = useState(null)
  const [settings, setSettings] = useState(null)
  const [workspace, setWorkspace] = useState(null)
  const [materials, setMaterials] = useState([])
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0)
  const [showWorkspacePanel, setShowWorkspacePanel] = useState(true)
  const [loading, setLoading] = useState(true)
  const [injectedPrompt, setInjectedPrompt] = useState(null)
  const activeProjectRef = useRef(currentProjectId)

  useEffect(() => {
    initializeApp()
  }, [])

  useEffect(() => {
    activeProjectRef.current = currentProjectId
  }, [currentProjectId])

  const initializeApp = async () => {
    await Promise.all([loadProjects(), loadSettings()])
  }

  const applyProjectSelection = (nextProjects, preferredProjectId = null) => {
    const nextProjectId = reconcileCurrentProjectId(
      nextProjects,
      preferredProjectId ?? activeProjectRef.current,
    )

    setProjects(nextProjects)
    setCurrentProjectId(nextProjectId)
    setCurrentProject(getCurrentProject(nextProjects, nextProjectId))
  }

  const loadProjects = async (preferredProjectId = null) => {
    try {
      setLoading(true)
      const res = await axios.get('/api/projects')
      applyProjectSelection(res.data, preferredProjectId)
    } catch (error) {
      console.error('加载项目失败:', error)
      alert('加载项目列表失败，请刷新页面重试')
    } finally {
      setLoading(false)
    }
  }

  const loadSettings = async () => {
    try {
      const res = await axios.get('/api/settings')
      setSettings(res.data)
    } catch (error) {
      console.error('加载设置失败:', error)
    }
  }

  useEffect(() => {
    loadWorkspace()
    loadMaterials()
  }, [currentProjectId, workspaceRefreshToken])

  const loadWorkspace = async () => {
    const requestProject = currentProjectId
    if (!requestProject) {
      setWorkspace(null)
      return
    }
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/workspace`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      setWorkspace(res.data)
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      console.error('加载工作区失败:', error)
      setWorkspace(null)
    }
  }

  const loadMaterials = async () => {
    const requestProject = currentProjectId
    if (!requestProject) {
      setMaterials([])
      return
    }
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/materials`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      setMaterials(res.data.materials || [])
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: activeProjectRef.current,
      })) {
        return
      }
      console.error('加载材料失败:', error)
      setMaterials([])
    }
  }

  const createProject = async (info) => {
    try {
      const res = await axios.post('/api/projects', info)
      const createdProject = res.data.project

      await loadProjects(createdProject.id)
      setWorkspaceRefreshToken(prev => prev + 1)
      return true
    } catch (error) {
      console.error('创建项目失败:', error)
      alert('创建项目失败，请重试')
      return false
    }
  }

  const deleteProject = async (projectId) => {
    try {
      await axios.delete(`/api/projects/${encodeURIComponent(projectId)}`)
      if (currentProjectId === projectId) {
        setCurrentProjectId(null)
        setCurrentProject(null)
        setWorkspace(null)
        setMaterials([])
      }
      await loadProjects()
      return true
    } catch (error) {
      console.error('删除项目失败:', error)
      alert('删除项目失败，请重试')
      return false
    }
  }

  const handleSelectProject = (project) => {
    if (isSameProjectSelection(currentProjectId, project?.id || null)) {
      return
    }
    setWorkspace(null)
    setMaterials([])
    setCurrentProjectId(project?.id || null)
    setCurrentProject(project || null)
  }

  const handleMaterialsMerged = (incomingMaterials) => {
    setMaterials(prev => mergeMaterials(prev, incomingMaterials))
    setWorkspace(prev => {
      if (!prev) {
        return prev
      }
      return {
        ...prev,
        materials: mergeMaterials(prev.materials || [], incomingMaterials),
      }
    })
  }

  const handleMaterialDeleted = (materialId) => {
    setMaterials(prev => removeMaterialById(prev, materialId))
    setWorkspace(prev => {
      if (!prev) {
        return prev
      }
      return {
        ...prev,
        materials: removeMaterialById(prev.materials || [], materialId),
      }
    })
  }

  if (loading) {
    return <div className="flex items-center justify-center h-screen"><div className="text-[#8888a8]">加载中...</div></div>
  }

  return (
    <ErrorBoundary>
      <Toaster position="top-right" />
      <div className="flex h-screen bg-[#0f0f23]">
        <Sidebar
          projects={projects}
          currentProjectId={currentProjectId}
          settings={settings}
          onSelectProject={handleSelectProject}
          onCreateProject={createProject}
          onDeleteProject={deleteProject}
          onSettingsSaved={loadSettings}
        />
        <ChatPanel
          projectId={currentProjectId}
          project={currentProject}
          settings={settings}
          workspace={workspace}
          materials={materials}
          onMaterialsMerged={handleMaterialsMerged}
          onProjectMutated={() => setWorkspaceRefreshToken(prev => prev + 1)}
          onToggleWorkspacePanel={() => setShowWorkspacePanel(!showWorkspacePanel)}
          injectedPrompt={injectedPrompt}
          onInjectedPromptConsumed={() => setInjectedPrompt(null)}
        />
        {showWorkspacePanel && (
          <WorkspacePanel
            projectId={currentProjectId}
            project={currentProject}
            workspace={workspace}
            materials={materials}
            refreshToken={workspaceRefreshToken}
            onMaterialDeleted={handleMaterialDeleted}
            onProjectMutated={() => setWorkspaceRefreshToken(prev => prev + 1)}
            onCheckpointSet={loadWorkspace}
            onInsertPrompt={(text) => setInjectedPrompt(text)}
          />
        )}
      </div>
    </ErrorBoundary>
  )
}

export default App
