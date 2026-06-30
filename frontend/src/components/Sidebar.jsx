import React, { useState } from 'react'
import axios from 'axios'
import SettingsModal from './SettingsModal'
import ProjectCreateModal from './ProjectCreateModal'
import { describeConnectionMode } from '../utils/connectionMode'
import { quotaLabel, quotaRatio } from '../utils/quotaFormat.js'
import { getStageName } from '../utils/workspaceSummary'
import { IconPlus, IconFile, IconTrash, IconShield, IconLogout, IconGear, IconSun, IconMoon } from './icons'

const PROJECT_TYPE_LABELS = {
  'strategy-consulting': '战略咨询',
  'market-research': '市场研究',
  'specialized-research': '专项研究',
  'management-document': '管理制度',
  'implementation-plan': '实施方案',
  'due-diligence': '尽职调查',
  'technical-bid': '技术标',
}

export default function Sidebar({
  projects,
  currentProjectId,
  settings,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onSettingsSaved,
  authUser,
  onLoggedOut,
  onOpenAdmin,
  theme,
  onToggleTheme,
  currentStageCode,
}) {
  const [showModal, setShowModal] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(null)

  const handleDelete = async (projectId) => {
    const success = await onDeleteProject(projectId)
    if (success) {
      setDeleteConfirm(null)
    }
  }

  const connection = describeConnectionMode(settings || {})

  return (
    <div className="w-[264px] flex-shrink-0 bg-bg border-r border-col flex flex-col">
      {/* 1) Wordmark 区 */}
      <div className="flex items-center gap-[9px]" style={{ padding: '18px 16px 12px' }}>
        <div className="flex items-center justify-center w-[26px] h-[26px] rounded-ibtn bg-accent text-white text-[13px] font-bold flex-shrink-0">
          R
        </div>
        <span className="text-[14.5px] font-bold text-text tracking-tight truncate">咨询报告助手</span>
      </div>

      {/* 2) 新建报告按钮区 */}
      <div className="px-3 pb-[10px]">
        <button
          onClick={() => setShowModal(true)}
          className="w-full h-[33px] rounded-ibtn bg-accent text-white text-13 font-medium flex items-center justify-center gap-[6px] hover:bg-accent/90"
        >
          <IconPlus size={14} />
          新建报告
        </button>
      </div>

      {/* 3) 项目列表 */}
      <div className="flex-1 overflow-y-auto px-2">
        <div className="text-[10.5px] font-bold tracking-[0.06em] text-t3" style={{ padding: '8px 8px 4px' }}>
          进行中
        </div>
        {projects.map((project) => {
          const isActive = currentProjectId === project.id
          const typeLabel = PROJECT_TYPE_LABELS[project.project_type] || ''
          // 副标题 = 「报告类型 · 阶段名」。活动项目用实时 workspace stage（每轮随
          // workspaceRefreshToken 刷新），其余项目用 list_projects 返回的 stage_code。
          // 两者都缺时只显示报告类型（向后兼容旧后端）。
          const stageCode = (isActive && currentStageCode) || project.stage_code
          const stageName = stageCode ? getStageName(stageCode) : ''
          const meta = [typeLabel, stageName].filter(Boolean).join(' · ')
          return (
            <div
              key={project.id}
              onClick={() => onSelectProject(project)}
              className={`flex items-center gap-[9px] px-[9px] py-2 rounded-ibtn mb-[2px] cursor-pointer ${
                isActive ? 'bg-sel' : 'hover:bg-card2'
              }`}
            >
              <IconFile
                size={15}
                className={isActive ? 'text-white flex-shrink-0' : 'text-t3 flex-shrink-0'}
              />
              <div className="min-w-0 flex-1">
                <div className={`text-13 truncate ${isActive ? 'font-medium text-white' : 'text-text'}`}>
                  {project.name}
                </div>
                {meta && (
                  <div className={`text-[10.5px] mt-[1px] ${isActive ? 'text-white/70' : 'text-t3'}`}>
                    {meta}
                  </div>
                )}
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  setDeleteConfirm(project.id)
                }}
                className="text-t3 hover:text-error flex-shrink-0"
                title="删除项目"
              >
                <IconTrash size={13} />
              </button>
            </div>
          )
        })}
      </div>

      {/* 4) 底部区 */}
      <div className="p-3 border-t border-col">
        {/* 4a) 账户行 — 仅 uid !== 'local' 显示 */}
        {authUser && authUser.uid !== 'local' && (
          <div className="flex justify-between items-center mb-[10px]">
            <div className="flex items-center gap-[7px] min-w-0">
              <div className="w-6 h-6 rounded-full bg-asoft text-asoftt flex items-center justify-center text-[11px] font-semibold flex-shrink-0">
                {authUser.username?.[0]?.toUpperCase() ?? '?'}
              </div>
              <span className="text-13 text-text font-medium truncate">{authUser.username}</span>
              {authUser.is_admin && (
                <span className="text-[9.5px] text-asoftt bg-asoft border border-asoftb px-[5px] rounded-[4px] flex-shrink-0">
                  管理员
                </span>
              )}
            </div>
            <div className="flex items-center gap-[2px] flex-shrink-0">
              {authUser?.is_admin && (
                <button
                  onClick={() => onOpenAdmin?.()}
                  title="用户管理"
                  className="w-[26px] h-[26px] flex items-center justify-center rounded-md text-t3 hover:bg-card2 hover:text-text"
                >
                  <IconShield size={14} />
                </button>
              )}
              <button
                onClick={async () => { try { await axios.post('/api/auth/logout') } catch (_) { /* ignore */ } onLoggedOut?.() }}
                title="登出"
                className="w-[26px] h-[26px] flex items-center justify-center rounded-md text-t3 hover:bg-card2 hover:text-text"
              >
                <IconLogout size={14} />
              </button>
            </div>
          </div>
        )}

        {/* 4b) 连接卡 (onClick 开设置) */}
        {/* 登出按钮只对 web 真实用户显示——桌面/本地（uid==="local"，合成账号）点登出会清掉
            authUser 把用户困在登录页（Codex review）。但今日额度对 local 同样适用（local 经
            managed 计费、默认 ¥5/天 cap、会被 reserve 拦），故额度行只要 daily_cap_yuan 是数字
            就显示（含 local），与登出门解耦。 */}
        <div
          className="bg-card2 border border-border rounded-[9px] cursor-pointer"
          style={{ padding: '10px 12px' }}
          onClick={() => setShowSettings(true)}
        >
          <div className="flex items-center gap-[6px]">
            <div className="w-[7px] h-[7px] rounded-full bg-success flex-shrink-0" />
            <span className="text-12 font-semibold text-text">{connection.title}</span>
          </div>
          <div className="text-[10.5px] text-t2 font-mono mt-[3px] truncate">{connection.subtitle}</div>
          {connection.helper && (
            <div className="text-[10.5px] text-t2 mt-[4px]">{connection.helper}</div>
          )}

          {/* 额度子区 — gated by daily_cap_yuan */}
          {authUser && (authUser.uid !== 'local' || typeof authUser.daily_cap_yuan === 'number') && typeof authUser.daily_cap_yuan === 'number' && (() => {
            // 统一归一：quotaLabel / quotaRatio / overCap 都用同一组 finite 值，避免边界不一致
            // （daily_cap_yuan 虽是 number 但可能是 NaN，typeof 仍过——codex NIT）。
            const used = Number.isFinite(authUser.today_cost_yuan) ? authUser.today_cost_yuan : 0
            const cap = Number.isFinite(authUser.daily_cap_yuan) ? authUser.daily_cap_yuan : 0
            const ratio = quotaRatio(used, cap)
            // 触额度上限（含 cap<=0 被 admin 设 0 封禁）= 耗尽，统一红色 100%——否则
            // cap=0 时 quotaRatio 返 0 会显示 0% 青色，与「已被封/无额度」矛盾（codex NIT）。
            const overCap = cap <= 0 || used >= cap
            const pct = overCap ? 100 : Math.round(ratio * 100)
            // 配色随用量升级：<80% 主题色、≥80% 警告色、耗尽红。
            const barClass = overCap ? 'bg-error' : ratio >= 0.8 ? 'bg-warn' : 'bg-abright'
            return (
              <div className="mt-[10px]">
                <div className="flex justify-between items-baseline mb-[5px]">
                  <span className="text-11 text-t2">今日额度</span>
                  <span className="text-11 text-text font-mono tabular-nums">
                    {quotaLabel(used, cap)} {pct}%
                  </span>
                </div>
                <div
                  className="h-[5px] rounded-[3px] bg-track overflow-hidden"
                  role="progressbar"
                  aria-label="今日额度使用进度"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={pct}
                >
                  <div
                    className={`h-full rounded-full transition-all ${barClass}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )
          })()}
        </div>

        {/* 4c) 底排：连接设置 + 主题切换 */}
        <div className="flex justify-between mt-[9px]">
          <button
            onClick={() => setShowSettings(true)}
            className="flex items-center gap-[6px] text-t2 text-12"
          >
            <IconGear size={14} />
            连接设置
          </button>
          <button
            onClick={onToggleTheme}
            className="flex items-center gap-[6px] text-t2 text-12"
          >
            {theme === 'dark' ? <IconSun size={14} /> : <IconMoon size={14} />}
            {theme === 'dark' ? '浅色' : '深色'}
          </button>
        </div>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onSaved={onSettingsSaved} />}

      {/* 5) 删除确认弹窗 */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60">
          <div className="bg-card border border-border rounded-win p-6 w-[min(384px,calc(100vw-32px))] shadow-popover">
            <h2 className="text-base font-semibold text-text mb-4">确认删除</h2>
            <p className="text-13 text-t2 mb-6">确定要删除这个项目吗？此操作无法撤销。</p>
            <div className="flex gap-2">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="flex-1 border border-border text-text rounded-btn py-2 hover:bg-card2"
              >
                取消
              </button>
              <button
                onClick={() => handleDelete(deleteConfirm)}
                className="flex-1 bg-error text-white rounded-btn py-2"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}

      {showModal && <ProjectCreateModal onClose={() => setShowModal(false)} onCreate={onCreateProject} />}
    </div>
  )
}
