import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Toaster } from 'react-hot-toast'
import Sidebar from './components/Sidebar'
import Login from './components/Login'
import ChatPanelPool from './components/ChatPanelPool'
import WorkspacePanel from './components/WorkspacePanel'
import ForcePasswordChange from './components/ForcePasswordChange'
import ErrorBoundary from './components/ErrorBoundary'
import axios from 'axios'
import { setUnauthedHandler } from './api'
import { shouldApplyProjectResponse } from './utils/projectRequestOwnership'
import { mergeMaterials, removeMaterialById } from './utils/chatMaterials'
import { getCurrentProject, isSameProjectSelection, reconcileCurrentProjectId } from './utils/projectSelection'
import { clampWorkspaceWidth, computeWorkspaceWidth, parseStoredWorkspaceWidth } from './utils/workspaceResize'
import { getInitialTheme, applyTheme, toggleTheme } from './utils/theme'
import { isCoarsePointer } from './utils/deviceMode'
import MobileShell from './components/MobileShell'
import OnboardingTour from './components/OnboardingTour'
import { runLogout } from './utils/chatPanelPoolCore'

const WORKSPACE_WIDTH_STORAGE_KEY = 'cra:workspaceWidth'

function App() {
  const [projects, setProjects] = useState([])
  const [currentProjectId, setCurrentProjectId] = useState(null)
  const [currentProject, setCurrentProject] = useState(null)
  const [settings, setSettings] = useState(null)
  const [workspace, setWorkspace] = useState(null)
  // workspace 归属的项目 id：切项目时 workspace 仍短暂持有旧项目数据（异步 fetch 间隙），
  // 侧栏副标题据此判断「实时 stage 是否属于当前活动项目」，避免旧 stage 瞬时覆盖新项目（codex NIT）。
  const [workspaceProjectId, setWorkspaceProjectId] = useState(null)
  const [materials, setMaterials] = useState([])
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0)
  const [showWorkspacePanel, setShowWorkspacePanel] = useState(true)
  // 左侧栏可整列收起（往左消失，聊天区变宽）；偏好持久化。收起后左上角留浮动按钮可再展开。
  const [showSidebar, setShowSidebar] = useState(() => {
    try { return localStorage.getItem('cra:showSidebar') !== '0' } catch { return true }
  })
  const [loading, setLoading] = useState(true)
  const [injectedPrompt, setInjectedPrompt] = useState(null)
  // 刚创建、待自动开场的项目集合。多个项目可并行创建/排队，不能用单值互相覆盖。
  const [pendingAutoStartProjectIds, setPendingAutoStartProjectIds] = useState(() => new Set())
  const [busyProjectIds, setBusyProjectIds] = useState(() => new Set())
  const [authUser, setAuthUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  // 管理控制台已是独立页面（/admin，2026-07-06）：新标签打开，保住主应用内存态
  // （ChatPanel 消息/工具记录在导航离开时会丢）。
  const openAdmin = () => { window.open('/admin', '_blank', 'noopener') }
  // 设备形态首屏锁定：触屏（pointer: coarse）走移动壳，桌面走原三栏。刻意只读一次、无 matchMedia 监听——
  // 桌面缩窗永不变形（移动适配按设备而非按宽度），且避免运行时切壳卸载子树丢状态。
  const [isMobile] = useState(() => isCoarsePointer())
  const [theme, setTheme] = useState(getInitialTheme)
  useEffect(() => { applyTheme(theme) }, [theme])
  const onToggleTheme = () => setTheme(t => toggleTheme(t))
  // 中右分栏宽度（px），可拖动；初始从 localStorage 读上次偏好（坏值回落默认 28rem）。
  const [workspaceWidth, setWorkspaceWidth] = useState(() =>
    parseStoredWorkspaceWidth(
      typeof localStorage !== 'undefined' ? localStorage.getItem(WORKSPACE_WIDTH_STORAGE_KEY) : null,
    ),
  )
  const currentProjectIdRef = useRef(currentProjectId)
  const authUserRef = useRef(authUser)
  const chatPanelRef = useRef(null)
  const workspacePanelRef = useRef(null)
  const containerRef = useRef(null)            // 主 flex 行：给 computeWorkspaceWidth 提供矩形
  const workspaceResizeCleanupRef = useRef(null) // 活跃拖动的 window 监听清理器
  const latestWorkspaceWidthRef = useRef(null)   // 拖动中最新宽度，松手时落 localStorage（避闭包旧值）
  const quotaRefreshSeqRef = useRef(0)   // 额度刷新请求序号：只让最后发起的 /me 回包落地（防同 uid 乱序覆盖）
  const projectsRefreshSeqRef = useRef(0)
  currentProjectIdRef.current = currentProjectId
  authUserRef.current = authUser

  useEffect(() => {
    setUnauthedHandler(() => {
      chatPanelRef.current?.abortAll?.()
      setAuthUser(null)
      setPendingAutoStartProjectIds(new Set())
      setBusyProjectIds(new Set())
    })
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

  const initializeApp = async () => {
    await Promise.all([loadProjects(), loadSettings()])
  }

  const applyProjectSelection = (nextProjects, preferredProjectId = null) => {
    const nextProjectId = reconcileCurrentProjectId(
      nextProjects,
      preferredProjectId ?? currentProjectIdRef.current,
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

  // 流生成期间的项目对账必须静默：顶层 loading 会卸载整个业务树，等价于杀掉所有后台流。
  // seq + uid 双守卫防乱序回包，以及 A 登出后旧请求覆盖 B 会话。
  const refreshProjectsSilently = async (preferredProjectId = null) => {
    const seq = ++projectsRefreshSeqRef.current
    const requestUid = authUserRef.current?.uid
    try {
      const res = await axios.get('/api/projects', { skipUnauthedHandler: true })
      if (seq !== projectsRefreshSeqRef.current) return false
      if (!requestUid || authUserRef.current?.uid !== requestUid) return false
      applyProjectSelection(res.data, preferredProjectId)
      return true
    } catch (error) {
      console.error('静默刷新项目失败:', error)
      return false
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
  const handleActiveProjectMutated = () => {
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
      setWorkspaceProjectId(null)
      return
    }
    try {
      const res = await axios.get(`/api/projects/${encodeURIComponent(requestProject)}/workspace`)
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: currentProjectIdRef.current,
      })) {
        return
      }
      setWorkspace(res.data)
      setWorkspaceProjectId(requestProject)
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: currentProjectIdRef.current,
      })) {
        return
      }
      console.error('加载工作区失败:', error)
      setWorkspace(null)
      setWorkspaceProjectId(null)
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
        activeProject: currentProjectIdRef.current,
      })) {
        return
      }
      setMaterials(res.data.materials || [])
    } catch (error) {
      if (!shouldApplyProjectResponse({
        requestProject,
        activeProject: currentProjectIdRef.current,
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
        // 自动开场标记：ChatPanel 确认新项目会话为空后触发 project_created 系统轮
        //（模型主动开启需求确认提问），消费后经 onAutoStartConsumed 清除。
        setPendingAutoStartProjectIds(prev => new Set(prev).add(createdProject.id))
        setProjects(prev => [...prev.filter(project => project.id !== createdProject.id), createdProject])
        setCurrentProjectId(createdProject.id)
        setCurrentProject(createdProject)
        await refreshProjectsSilently(createdProject.id)
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
    const admission = chatPanelRef.current?.tryBeginDelete?.(projectId)
    if (admission?.status === 'uploading') {
      alert('项目正在导入材料，请稍候再删')
      return false
    }
    if (admission?.status === 'deleting') return false
    if (!admission || admission.status !== 'started') {
      alert('项目当前无法删除，请稍后重试')
      return false
    }
    let forgotten = false
    try {
      chatPanelRef.current?.abortProjectWork?.(projectId)
      await axios.delete(`/api/projects/${encodeURIComponent(projectId)}`)
      forgotten = true
      setPendingAutoStartProjectIds(prev => {
        const next = new Set(prev)
        next.delete(projectId)
        return next
      })
      const remainingProjects = projects.filter(project => project.id !== projectId)
      applyProjectSelection(remainingProjects)
      if (currentProjectIdRef.current === projectId) {
        setWorkspace(null)
        setWorkspaceProjectId(null)
        setMaterials([])
      }
      void refreshProjectsSilently()
      return true
    } catch (error) {
      console.error('删除项目失败:', error)
      alert(error?.response?.data?.detail || '删除项目失败，请重试')
      return false
    } finally {
      chatPanelRef.current?.finishDelete?.(admission.token, { forgotten })
    }
  }

  const handleSelectProject = (project) => {
    if (isSameProjectSelection(currentProjectId, project?.id || null)) {
      return
    }
    const proceed = () => {
      setWorkspace(null)
      setWorkspaceProjectId(null)
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

  // 文件内链（2026-07-09）：聊天区 pill / 正文文件名点击 → 确保工作区面板可见 → 打开该文件。
  // 面板收起时先展开；ref 要等重新 mount 后才有，setTimeout(0) 排到 commit 之后再调。
  const handleOpenWorkspaceFile = (path) => {
    if (!path) return
    if (!showWorkspacePanel) {
      setShowWorkspacePanel(true)
      setTimeout(() => workspacePanelRef.current?.openFile(path), 0)
      return
    }
    workspacePanelRef.current?.openFile(path)
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

  const handleActiveMaterialsMerged = (incomingMaterials) => {
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

  // Pool 回调显式带 pid。后台项目完成时只刷新全局额度/项目摘要，绝不把材料或 workspace
  // 写进当前项目视图；活动项目才委托既有的视图更新契约。
  const handlePanelMaterialsMerged = (projectId, incomingMaterials) => {
    if (projectId === currentProjectIdRef.current) {
      handleActiveMaterialsMerged(incomingMaterials)
    }
    refreshAuthQuota()
    void refreshProjectsSilently()
  }

  const handlePanelProjectMutated = (projectId) => {
    if (projectId === currentProjectIdRef.current) {
      handleActiveProjectMutated()
    } else {
      refreshAuthQuota()
    }
    void refreshProjectsSilently()
  }

  const consumeAutoStartProject = (projectId) => {
    setPendingAutoStartProjectIds(prev => {
      const next = new Set(prev)
      next.delete(projectId)
      return next
    })
  }

  const handleLogoutIntent = () => {
    void runLogout({
      abortAll: () => chatPanelRef.current?.abortAll?.(),
      requestLogout: () => axios.post('/api/auth/logout'),
      clearSession: () => {
        setAuthUser(null)
        setPendingAutoStartProjectIds(new Set())
        setBusyProjectIds(new Set())
      },
    }).catch(error => console.error('登出请求失败:', error))
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
      {isMobile ? (
        <MobileShell
          projects={projects}
          currentProjectId={currentProjectId}
          settings={settings}
          authUser={authUser}
          theme={theme}
          project={currentProject}
          workspace={workspace}
          materials={materials}
          workspaceRefreshToken={workspaceRefreshToken}
          injectedPrompt={injectedPrompt}
          pendingAutoStartProjectIds={pendingAutoStartProjectIds}
          onAutoStartConsumed={consumeAutoStartProject}
          chatPanelPoolRef={chatPanelRef}
          busyProjectIds={busyProjectIds}
          workspaceStageCode={workspaceProjectId === currentProjectId ? workspace?.stage_code : undefined}
          onSelectProject={handleSelectProject}
          onCreateProject={createProject}
          onDeleteProject={deleteProject}
          onSettingsSaved={loadSettings}
          onLoggedOut={handleLogoutIntent}
          onOpenAdmin={openAdmin}
          onToggleTheme={onToggleTheme}
          onMaterialsMerged={handleActiveMaterialsMerged}
          onPanelMaterialsMerged={handlePanelMaterialsMerged}
          onMaterialDeleted={handleMaterialDeleted}
          onProjectMutated={handleActiveProjectMutated}
          onPanelProjectMutated={handlePanelProjectMutated}
          onBusyIndicatorChange={setBusyProjectIds}
          onCheckpointSet={loadWorkspace}
          onInsertPrompt={(text) => setInjectedPrompt(text)}
          onInjectedPromptConsumed={() => setInjectedPrompt(null)}
        />
      ) : (
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
            onLoggedOut={handleLogoutIntent}
            onOpenAdmin={openAdmin}
            theme={theme}
            onToggleTheme={onToggleTheme}
            currentStageCode={workspaceProjectId === currentProjectId ? workspace?.stage_code : undefined}
            busyProjectIds={busyProjectIds}
          />
        )}
        {/* 可调区域（不含固定宽 Sidebar）：clamp 的 MIN_CHAT_WIDTH 须按这个区域预留。 */}
        <div ref={setContainerRef} className="flex flex-1 min-w-0">
          <ChatPanelPool
            ref={chatPanelRef}
            activeProjectId={currentProjectId}
            panelProps={{
              settings,
              onToggleSidebar: () => toggleSidebar(),
              onToggleWorkspacePanel: handleToggleWorkspacePanel,
              onOpenWorkspaceFile: handleOpenWorkspaceFile,
            }}
            activeOnlyProps={{
              project: currentProject,
              workspace,
              materials,
              injectedPrompt,
              onInjectedPromptConsumed: () => setInjectedPrompt(null),
            }}
            pendingAutoStartProjectIds={pendingAutoStartProjectIds}
            onAutoStartConsumed={consumeAutoStartProject}
            onPanelMaterialsMerged={handlePanelMaterialsMerged}
            onPanelProjectMutated={handlePanelProjectMutated}
            onBusyIndicatorChange={setBusyProjectIds}
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
                workspace={workspace}
                materials={materials}
                refreshToken={workspaceRefreshToken}
                width={workspaceWidth}
                onMaterialsMerged={handleActiveMaterialsMerged}
                onMaterialDeleted={handleMaterialDeleted}
                onProjectMutated={handleActiveProjectMutated}
                onCheckpointSet={loadWorkspace}
                onInsertPrompt={(text) => setInjectedPrompt(text)}
                onSendPrompt={(text) => chatPanelRef.current?.sendUserMessage(text) ?? false}
                onTriggerSystemTurn={(triggerType, metadata) => chatPanelRef.current?.triggerSystemTurn(triggerType, metadata)}
                onDropPendingReviewTriggers={(triggerType) => chatPanelRef.current?.dropPendingReviewTriggers(triggerType)}
                beginUpload={(projectId) => chatPanelRef.current?.beginUpload(projectId) ?? null}
                endUpload={(token) => chatPanelRef.current?.endUpload(token)}
              />
            </>
          )}
        </div>
      </div>
      )}
      {/* 初次使用引导（终身一次）：严格 === false 才弹（老会话 /me 无该字段 → undefined 不弹，
          fail-closed 不打扰）。onDone 用 {...prev} 更新 onboarded —— init effect 依赖仍是
          [uid, must_change_password]，不会因此重挂主界面（黑屏雷区，见上方 effect 注释）。 */}
      {authUser.onboarded === false && (
        <OnboardingTour onDone={() => setAuthUser(prev => (prev ? { ...prev, onboarded: true } : prev))} />
      )}
    </ErrorBoundary>
  )
}

export default App
