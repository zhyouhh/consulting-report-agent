import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Toaster } from 'react-hot-toast'
import Sidebar from './components/Sidebar'
import Login from './components/Login'
import ChatPanel from './components/ChatPanel'
import WorkspacePanel from './components/WorkspacePanel'
import AdminPanel from './components/AdminPanel'
import ForcePasswordChange from './components/ForcePasswordChange'
import ErrorBoundary from './components/ErrorBoundary'
import { IconSidebar } from './components/icons'
import axios from 'axios'
import { setUnauthedHandler } from './api'
import { shouldApplyProjectResponse } from './utils/projectRequestOwnership'
import { mergeMaterials, removeMaterialById } from './utils/chatMaterials'
import { getCurrentProject, isSameProjectSelection, reconcileCurrentProjectId } from './utils/projectSelection'
import { clampWorkspaceWidth, computeWorkspaceWidth, parseStoredWorkspaceWidth } from './utils/workspaceResize'
import { getInitialTheme, applyTheme, toggleTheme } from './utils/theme'

const WORKSPACE_WIDTH_STORAGE_KEY = 'cra:workspaceWidth'

function App() {
  const [projects, setProjects] = useState([])
  const [currentProjectId, setCurrentProjectId] = useState(null)
  const [currentProject, setCurrentProject] = useState(null)
  const [settings, setSettings] = useState(null)
  const [workspace, setWorkspace] = useState(null)
  const [materials, setMaterials] = useState([])
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0)
  const [showWorkspacePanel, setShowWorkspacePanel] = useState(true)
  // 左侧栏可整列收起（往左消失，聊天区变宽）；偏好持久化。收起后左上角留浮动按钮可再展开。
  const [showSidebar, setShowSidebar] = useState(() => {
    try { return localStorage.getItem('cra:showSidebar') !== '0' } catch { return true }
  })
  const [loading, setLoading] = useState(true)
  const [injectedPrompt, setInjectedPrompt] = useState(null)
  const [authUser, setAuthUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [showAdmin, setShowAdmin] = useState(false)
  const [theme, setTheme] = useState(getInitialTheme)
  useEffect(() => { applyTheme(theme) }, [theme])
  const onToggleTheme = () => setTheme(t => toggleTheme(t))
  // 中右分栏宽度（px），可拖动；初始从 localStorage 读上次偏好（坏值回落默认 28rem）。
  const [workspaceWidth, setWorkspaceWidth] = useState(() =>
    parseStoredWorkspaceWidth(
      typeof localStorage !== 'undefined' ? localStorage.getItem(WORKSPACE_WIDTH_STORAGE_KEY) : null,
    ),
  )
  const activeProjectRef = useRef(currentProjectId)
  const chatPanelRef = useRef(null)
  const workspacePanelRef = useRef(null)
  const containerRef = useRef(null)            // 主 flex 行：给 computeWorkspaceWidth 提供矩形
  const workspaceResizeCleanupRef = useRef(null) // 活跃拖动的 window 监听清理器
  const latestWorkspaceWidthRef = useRef(null)   // 拖动中最新宽度，松手时落 localStorage（避闭包旧值）
  const quotaRefreshSeqRef = useRef(0)   // 额度刷新请求序号：只让最后发起的 /me 回包落地（防同 uid 乱序覆盖）

  useEffect(() => {
    setUnauthedHandler(() => setAuthUser(null))
    axios.get('/api/auth/me').then((r) => { setAuthUser(r.data) }).catch(() => {}).finally(() => setAuthChecked(true))
  }, [])

  // 主界面数据初始化（项目/设置）effect 驱动，按 must_change_password gate（codex NIT 1）：
  // 强制改密用户登录后，业务路由会被后端 403（Phase 4 强制）→ 若此时 initializeApp 会闪
  // 「加载项目列表失败」错误弹窗。故只在「已登录且无需强制改密」时才加载；改密成功刷新
  // authUser（must_change_password 变 false）后此 effect 重跑，这时才加载主界面。
  // 注意：依赖必须是「稳定身份字段（uid + must_change_password）」而非整个 authUser 对象：
  // refreshAuthQuota 每轮用 {...prev, 新额度} 造新引用，若依赖整 authUser → 每轮重跑
  // initializeApp → loadProjects 置 loading=true → 命中 if(loading) 早返回 → 整树卸载重挂
  // （黑屏闪 + ChatPanel 内存里的消息/工具调用记录全丢）。额度只是显示数据，不该触发重初始化。
  useEffect(() => {
    if (authUser && !authUser.must_change_password) {
      initializeApp()
    }
  }, [authUser?.uid, authUser?.must_change_password])

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

  // 侧边栏「今日额度」原本只随初始 /me 拉一次 → 用掉额度后变成陈旧快照（而管理面板每次
  // 打开都重查同一后端函数，于是两处对不上）。聊天每轮结束 / 窗口重新聚焦时轻量刷新 /me，
  // 只并入 cost 字段、不动 must_change_password 等其它态；prev 为空（已登出）则不复活。
  const refreshAuthQuota = () => {
    const seq = ++quotaRefreshSeqRef.current
    // skipUnauthedHandler：背景轮询不触发全局 401 登出（防旧用户在途 /me 误踢新用户，codex BLOCKER）。
    axios.get('/api/auth/me', { skipUnauthedHandler: true })
      .then((r) => setAuthUser((prev) => {
        // 1) 序号守卫：有更晚发起的刷新（current 已自增）则丢弃本次旧回包——focus + 轮结束
        //    会并发发 /me，旧请求晚返回会把新额度覆盖回旧值，反而重造「陈旧」（codex BLOCKER）。
        if (seq !== quotaRefreshSeqRef.current) return prev
        // 2) uid 守卫：回包须仍属当前登录用户。在途 /me（用户 A）若在 A 登出、B 登录后才返回，
        //    prev 已是 B → 不可把 A 的额度串进 B。uid 不符 / 已登出则原样返回。
        if (!prev || r.data?.uid !== prev.uid) return prev
        return { ...prev, today_cost_yuan: r.data.today_cost_yuan, daily_cap_yuan: r.data.daily_cap_yuan }
      }))
      .catch(() => { /* 背景轮询：401 已 skipUnauthedHandler（见上），所有错误一律静默忽略、不擦已有额度 */ })
  }

  // 统一的「项目被改动」回调：刷 workspace + 刷额度。聊天轮结束、独立审查后的系统轮、
  // 工作区文件保存/checkpoint 都经此 → 任一计费路径完成后 sidebar 额度都即时更新
  // （不止依赖 focus 兜底）。refreshAuthQuota 只动 cost 字段、不触发 initializeApp，安全。
  const handleProjectMutated = () => {
    setWorkspaceRefreshToken(prev => prev + 1)
    refreshAuthQuota()
  }

  // 同样只依赖稳定身份字段：onFocus 只读「已登录且无需改密」（额度变化不影响判断），
  // 避免每轮额度刷新都 re-subscribe 一次监听器。
  useEffect(() => {
    const onFocus = () => { if (authUser && !authUser.must_change_password) refreshAuthQuota() }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [authUser?.uid, authUser?.must_change_password])

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

      // 切到新项目也是一条「离开当前编辑」的路径，必须过 dirty guard（与 handleSelectProject 同）——
      // 否则编辑态下点「新建报告」会让旧草稿悬挂、之后保存打到新项目（codex 前端审 BLOCKER 1）。
      const proceed = async () => {
        await loadProjects(createdProject.id)
        setWorkspaceRefreshToken(prev => prev + 1)
      }
      const wp = workspacePanelRef.current
      if (wp?.attemptLeave) { wp.attemptLeave(proceed) } else { await proceed() }
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
    const proceed = () => {
      setWorkspace(null)
      setMaterials([])
      setCurrentProjectId(project?.id || null)
      setCurrentProject(project || null)
    }
    // dirty 时弹三按钮、把切项目挂起（保存/放弃后再切）；allow 立即切；保存中则拦下。
    const wp = workspacePanelRef.current
    if (wp?.attemptLeave) { wp.attemptLeave(proceed) } else { proceed() }
  }

  const handleToggleWorkspacePanel = () => {
    const proceed = () => setShowWorkspacePanel((v) => !v)
    const wp = workspacePanelRef.current
    // 仅「当前显示 → 隐藏」是离开路径（隐藏会 unmount 编辑器）；dirty 弹三按钮、把隐藏挂起。
    if (showWorkspacePanel && wp?.attemptLeave) { wp.attemptLeave(proceed); return }
    proceed()
  }

  // 容器 = 「聊天区 + 分隔条 + 工作区」可调区域（排除固定宽的左侧 Sidebar）——clamp 须按这个区域
  // 预留 MIN_CHAT_WIDTH，否则把整窗宽（含 Sidebar）算进去会让聊天区被挤到 ~100px。容器在主界面
  // 渲染（登录后）才挂载，故用 callback ref：挂载即用真实宽度把存储宽度夹一次（修「存的宽超出当前窗口、
  // 启动就把聊天区挤没」）。
  const setContainerRef = useCallback((node) => {
    containerRef.current = node
    if (node) {
      const rect = node.getBoundingClientRect()
      setWorkspaceWidth((prev) => clampWorkspaceWidth(prev, rect.width))
    }
  }, [])

  // 中右分隔条拖动：沿用 FilePreviewPanel 上下拖动模式（mousedown 绑 window mousemove/mouseup，
  // cleanup ref 防重复绑定/泄漏）。工作区宽度变 → ChatPanel(flex-1) 与其内部输入框/用量框
  // 自动重排，无需手动同步宽度。松手时把最终宽度落 localStorage 记住偏好。
  const startWorkspaceResize = (e) => {
    e.preventDefault()
    workspaceResizeCleanupRef.current?.()
    const prevUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none' // 拖动期间不蓝选文本
    const onMove = (ev) => {
      const next = computeWorkspaceWidth(ev.clientX, containerRef.current?.getBoundingClientRect())
      latestWorkspaceWidthRef.current = next
      setWorkspaceWidth(next)
    }
    const cleanup = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', cleanup)
      document.body.style.userSelect = prevUserSelect // 还原拖动前的原值，不抹掉已有内联值
      workspaceResizeCleanupRef.current = null
      if (latestWorkspaceWidthRef.current != null) {
        try { localStorage.setItem(WORKSPACE_WIDTH_STORAGE_KEY, String(latestWorkspaceWidthRef.current)) } catch { /* 隐私模式忽略 */ }
      }
    }
    workspaceResizeCleanupRef.current = cleanup
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', cleanup)
  }

  // 卸载兜底：拖动中途卸载不留泄漏监听。
  useEffect(() => () => workspaceResizeCleanupRef.current?.(), [])

  // 窗口缩小后重新夹宽度，防工作区占满把聊天区挤没（拖动时的 clamp 不覆盖 resize 场景）。
  useEffect(() => {
    const onResize = () => {
      const rect = containerRef.current?.getBoundingClientRect()
      setWorkspaceWidth((prev) => clampWorkspaceWidth(prev, rect?.width))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // 侧栏显隐改变了可调区域宽度：显隐后按新容器宽度重夹一次，防再次显示侧栏时把聊天区挤到
  // MIN_CHAT 以下（与 window resize 重夹同理，只是触发源是侧栏 toggle）。
  useEffect(() => {
    const rect = containerRef.current?.getBoundingClientRect()
    setWorkspaceWidth((prev) => clampWorkspaceWidth(prev, rect?.width))
  }, [showSidebar])

  // 收起/展开左侧栏并持久化偏好。next 省略则翻转。
  const toggleSidebar = useCallback((next) => {
    setShowSidebar((prev) => {
      const v = typeof next === 'boolean' ? next : !prev
      try { localStorage.setItem('cra:showSidebar', v ? '1' : '0') } catch { /* 隐私模式忽略 */ }
      return v
    })
  }, [])

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

  if (!authChecked) return <div className="flex items-center justify-center h-screen bg-bg"><div className="text-t2">加载中...</div></div>
  // 登录成功只 setAuthUser；初始化交给 authUser effect（按 must_change_password gate，codex NIT 1）。
  if (!authUser) return <Login onAuthed={(u) => { setAuthUser(u) }} />

  // B3 Task 17：首次登录强制改密——登录后、主界面前的硬门。改完刷新 authUser 才放行；
  // 不可关（无 onClose）。后端 Task 14 已对业务路由 403 双保险。
  if (authUser.must_change_password) {
    return <ForcePasswordChange onChanged={async () => {
      const r = await axios.get('/api/auth/me'); setAuthUser(r.data)
    }} />
  }

  if (loading) {
    return <div className="flex items-center justify-center h-screen bg-bg"><div className="text-t2">加载中...</div></div>
  }

  return (
    <ErrorBoundary>
      <Toaster position="top-right" />
      <div className="flex h-screen bg-bg">
        {showSidebar && (
          <Sidebar
            projects={projects}
            currentProjectId={currentProjectId}
            settings={settings}
            onSelectProject={handleSelectProject}
            onCreateProject={createProject}
            onDeleteProject={deleteProject}
            onSettingsSaved={loadSettings}
            authUser={authUser}
            onLoggedOut={() => setAuthUser(null)}
            onOpenAdmin={() => setShowAdmin(true)}
            theme={theme}
            onToggleTheme={onToggleTheme}
            onCollapseSidebar={() => toggleSidebar(false)}
          />
        )}
        {!showSidebar && (
          <button
            onClick={() => toggleSidebar(true)}
            title="显示侧栏"
            aria-label="显示侧栏"
            className="fixed left-2 top-2 z-40 flex h-8 w-8 items-center justify-center rounded-ibtn border border-border bg-card2 text-t2 shadow-card hover:text-text"
          >
            <IconSidebar size={16} />
          </button>
        )}
        {/* 可调区域（不含固定宽 Sidebar）：clamp 的 MIN_CHAT_WIDTH 须按这个区域预留。 */}
        <div ref={setContainerRef} className="flex flex-1 min-w-0">
          <ChatPanel
            ref={chatPanelRef}
            projectId={currentProjectId}
            project={currentProject}
            settings={settings}
            workspace={workspace}
            materials={materials}
            onMaterialsMerged={handleMaterialsMerged}
            onProjectMutated={handleProjectMutated}
            onToggleWorkspacePanel={handleToggleWorkspacePanel}
            injectedPrompt={injectedPrompt}
            onInjectedPromptConsumed={() => setInjectedPrompt(null)}
          />
          {showWorkspacePanel && (
            <>
              {/* 中右分隔条：左右拖动调整工作区宽度。手柄随面板一起显隐。 */}
              <div
                onMouseDown={startWorkspaceResize}
                role="separator"
                aria-orientation="vertical"
                className="w-1.5 cursor-col-resize bg-col hover:bg-abright/40 flex-shrink-0"
                title="拖动调整宽度"
              />
              <WorkspacePanel
                ref={workspacePanelRef}
                projectId={currentProjectId}
                project={currentProject}
                workspace={workspace}
                materials={materials}
                refreshToken={workspaceRefreshToken}
                width={workspaceWidth}
                onMaterialDeleted={handleMaterialDeleted}
                onProjectMutated={handleProjectMutated}
                onCheckpointSet={loadWorkspace}
                onInsertPrompt={(text) => setInjectedPrompt(text)}
                onTriggerSystemTurn={(triggerType, metadata) => chatPanelRef.current?.triggerSystemTurn(triggerType, metadata)}
                onDropPendingReviewTriggers={(triggerType) => chatPanelRef.current?.dropPendingReviewTriggers(triggerType)}
              />
            </>
          )}
        </div>
        {showAdmin && authUser?.is_admin && <AdminPanel onClose={() => setShowAdmin(false)} />}
      </div>
    </ErrorBoundary>
  )
}

export default App
