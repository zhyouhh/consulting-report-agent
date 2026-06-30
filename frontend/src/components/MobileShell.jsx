import { useRef, useState } from 'react'
import ChatPanel from './ChatPanel'
import Sidebar from './Sidebar'
import WorkspacePanel from './WorkspacePanel'
import { nextDrawerState, DRAWER_NONE, DRAWER_LEFT, DRAWER_RIGHT } from '../utils/deviceMode'

// 移动端壳（spec 4）：聊天占满 + 左/右抽屉覆盖，复用桌面三面板，只换摆法。
// 抽屉滑动用 left/right 定位，刻意不用会生成 containing block 的 CSS 变换（保内部 fixed 弹窗满屏）。
// 常驻挂载（关闭不卸载，上传/审查存活）；根 100dvh + composer safe-area。
export default function MobileShell(props) {
  const {
    projects, currentProjectId, settings, authUser, theme,
    project, workspace, materials, workspaceRefreshToken, injectedPrompt, workspaceStageCode,
    onSelectProject, onCreateProject, onDeleteProject, onSettingsSaved,
    onLoggedOut, onOpenAdmin, onToggleTheme,
    onMaterialsMerged, onMaterialDeleted, onProjectMutated, onCheckpointSet,
    onInsertPrompt, onInjectedPromptConsumed,
  } = props

  const chatPanelRef = useRef(null)
  const [drawer, setDrawer] = useState(DRAWER_NONE)
  const closeAll = () => setDrawer(DRAWER_NONE)
  const toggleLeft = () => setDrawer((d) => nextDrawerState(d, 'toggleLeft'))
  const toggleRight = () => setDrawer((d) => nextDrawerState(d, 'toggleRight'))

  // Sidebar 回调包装：动作后自动关左抽屉，Sidebar 本体零改。
  const handleSelectProject = (p) => { onSelectProject(p); closeAll() }
  const handleCreateProject = async (p) => { const ok = await onCreateProject(p); if (ok) closeAll(); return ok }
  const handleLoggedOut = () => { closeAll(); onLoggedOut() }
  const handleOpenAdmin = () => { closeAll(); onOpenAdmin() }
  // 设置保存后关左抽屉（与 select/create/logout/admin 同模式；动作完成回到聊天）
  const handleSettingsSaved = (...args) => { const r = onSettingsSaved?.(...args); closeAll(); return r }
  // onToggleTheme 刻意不包装/不 closeAll：切主题是就地开关，用户可能连续切/继续浏览，关抽屉反而打断。
  // 删除项目刻意不关左抽屉：删完顺手在列表挑下一个。

  // 审查完成：触发主聊天汇报轮后关右抽屉，落到聊天。
  const handleTriggerSystemTurn = (t, m) => { chatPanelRef.current?.triggerSystemTurn(t, m); closeAll() }
  const handleDropPendingReviewTriggers = (t) => chatPanelRef.current?.dropPendingReviewTriggers(t)
  // 继续扩写/回退插入 prompt 后关右抽屉，否则输入框在抽屉背后像没反应。
  const handleInsertPrompt = (text) => { onInsertPrompt(text); closeAll() }

  return (
    <div className="relative w-screen bg-bg overflow-hidden" style={{ height: '100dvh' }}>
      {/* 聊天主区：复用 ChatPanel 自带顶栏（侧栏按钮/工作区按钮接抽屉）。safe-area 由本壳承担。 */}
      <div className="absolute inset-0 flex flex-col min-h-0" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <ChatPanel
          ref={chatPanelRef}
          projectId={currentProjectId}
          project={project}
          settings={settings}
          workspace={workspace}
          materials={materials}
          onMaterialsMerged={onMaterialsMerged}
          onProjectMutated={onProjectMutated}
          onToggleSidebar={toggleLeft}
          onToggleWorkspacePanel={toggleRight}
          injectedPrompt={injectedPrompt}
          onInjectedPromptConsumed={onInjectedPromptConsumed}
        />
      </div>

      {/* scrim：抽屉打开时盖聊天、点击关闭。dark:bg-scrim/60 是唯一被 darkClassGuard 允许的 dark: 类。 */}
      {drawer !== DRAWER_NONE && (
        <div onClick={closeAll} aria-hidden="true" className="absolute inset-0 z-20 bg-scrim/40 dark:bg-scrim/60" />
      )}

      {/* 左抽屉 = Sidebar。显式 w-[264px] h-full flex 给子面板高度基准，off-canvas left，常驻挂载。 */}
      <div
        className="absolute top-0 left-0 z-30 h-full w-[264px] flex transition-[left] duration-200 ease-out"
        style={{ left: drawer === DRAWER_LEFT ? '0' : '-110%', visibility: drawer === DRAWER_LEFT ? 'visible' : 'hidden' }}
      >
        <Sidebar
          projects={projects}
          currentProjectId={currentProjectId}
          settings={settings}
          onSelectProject={handleSelectProject}
          onCreateProject={handleCreateProject}
          onDeleteProject={onDeleteProject}
          onSettingsSaved={handleSettingsSaved}
          authUser={authUser}
          onLoggedOut={handleLoggedOut}
          onOpenAdmin={handleOpenAdmin}
          theme={theme}
          onToggleTheme={onToggleTheme}
          currentStageCode={workspaceStageCode}
        />
      </div>

      {/* 右抽屉 = WorkspacePanel。显式 w-[min(100vw,28rem)] h-full flex overflow-hidden 给 width 基准 + 拉满高度，off-canvas right，常驻挂载。 */}
      <div
        className="absolute top-0 right-0 z-30 h-full w-[min(100vw,28rem)] flex overflow-hidden transition-[right] duration-200 ease-out"
        style={{ right: drawer === DRAWER_RIGHT ? '0' : '-110%', visibility: drawer === DRAWER_RIGHT ? 'visible' : 'hidden' }}
      >
        <WorkspacePanel
          isMobile={true}
          width="100%"
          projectId={currentProjectId}
          workspace={workspace}
          materials={materials}
          refreshToken={workspaceRefreshToken}
          onMaterialsMerged={onMaterialsMerged}
          onMaterialDeleted={onMaterialDeleted}
          onProjectMutated={onProjectMutated}
          onCheckpointSet={onCheckpointSet}
          onInsertPrompt={handleInsertPrompt}
          onTriggerSystemTurn={handleTriggerSystemTurn}
          onDropPendingReviewTriggers={handleDropPendingReviewTriggers}
        />
      </div>
    </div>
  )
}
