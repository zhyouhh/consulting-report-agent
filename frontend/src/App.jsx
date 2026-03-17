import React, { useState, useEffect } from 'react'
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
  const [showPreview, setShowPreview] = useState(true)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadProjects()
  }, [])

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

  if (loading) {
    return <div className="flex items-center justify-center h-screen"><div className="text-[#8888a8]">加载中...</div></div>
  }

  return (
    <ErrorBoundary>
      <div className="flex h-screen bg-[#0f0f23]">
        <Sidebar
          projects={projects}
          currentProject={currentProject}
          onSelectProject={setCurrentProject}
          onCreateProject={createProject}
        />
        <ChatPanel
          project={currentProject}
          onTogglePreview={() => setShowPreview(!showPreview)}
        />
        {showPreview && <PreviewPanel project={currentProject} />}
      </div>
    </ErrorBoundary>
  )
}

export default App
