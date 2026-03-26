import React, { useState, useEffect } from 'react'
import { Toaster } from 'react-hot-toast'
import Sidebar from './components/Sidebar'
import ChatPanel from './components/ChatPanel'
import PreviewPanel from './components/PreviewPanel'
import axios from 'axios'

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
  const [currentProject, setCurrentProject] = useState(null)
  const [settings, setSettings] = useState(null)
  const [showPreview, setShowPreview] = useState(true)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    initializeApp()
  }, [])

  const initializeApp = async () => {
    await Promise.all([loadProjects(), loadSettings()])
  }

  const loadProjects = async () => {
    try {
      setLoading(true)
      const res = await axios.get('/api/projects')
      setProjects(res.data)
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

  const createProject = async (info) => {
    try {
      await axios.post('/api/projects', info)
      await loadProjects()
      setCurrentProject(info.name)
      return true
    } catch (error) {
      console.error('创建项目失败:', error)
      alert('创建项目失败，请重试')
      return false
    }
  }

  const deleteProject = async (projectName) => {
    try {
      await axios.delete(`/api/projects/${projectName}`)
      if (currentProject === projectName) {
        setCurrentProject(null)
      }
      await loadProjects()
      return true
    } catch (error) {
      console.error('删除项目失败:', error)
      alert('删除项目失败，请重试')
      return false
    }
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
          currentProject={currentProject}
          settings={settings}
          onSelectProject={setCurrentProject}
          onCreateProject={createProject}
          onDeleteProject={deleteProject}
          onSettingsSaved={loadSettings}
        />
        <ChatPanel
          project={currentProject}
          settings={settings}
          onTogglePreview={() => setShowPreview(!showPreview)}
        />
        {showPreview && <PreviewPanel project={currentProject} />}
      </div>
    </ErrorBoundary>
  )
}

export default App
