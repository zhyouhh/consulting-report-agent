import React, { useState } from 'react'
import axios from 'axios'
import SettingsModal from './SettingsModal'
import ProjectCreateModal from './ProjectCreateModal'
import { describeConnectionMode } from '../utils/connectionMode'
import { quotaLabel, quotaRatio } from '../utils/quotaFormat.js'

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
    <div className="w-64 bg-[#1a1a2e] border-r border-[#2a2a4a] flex flex-col">
      <div className="p-4 border-b border-[#2a2a4a]">
        <h1 className="text-lg font-semibold text-[#e2e2f0]">咨询报告助手</h1>
      </div>

      <div className="p-4">
        <button
          onClick={() => setShowModal(true)}
          className="w-full bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
        >
          + 新建报告
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4">
        {projects.map((project) => (
          <div
            key={project.id}
            className={`p-3 mb-2 rounded-lg flex items-center justify-between ${
              currentProjectId === project.id ? 'bg-[#1e1e4a] border border-[#3b5998]' : 'hover:bg-[#222244]'
            }`}
          >
            <div
              onClick={() => onSelectProject(project)}
              className="flex-1 cursor-pointer font-medium text-sm text-[#e2e2f0]"
            >
              {project.name}
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setDeleteConfirm(project.id)
              }}
              className="text-red-400 hover:text-red-300 ml-2"
            >
              🗑
            </button>
          </div>
        ))}
      </div>

      <div className="p-4 border-t border-[#2a2a4a]">
        <div className="mb-2 px-3 py-2 rounded-lg bg-[#15162d] border border-[#2f3158]">
          <div className="text-xs text-[#64ffda] font-medium">{connection.title}</div>
          <div className="text-[11px] text-[#8f93c9] mt-1">{connection.subtitle}</div>
        </div>
        {connection.helper && (
          <div className="mb-2 text-[11px] leading-5 text-[#7f84b8]">
            {connection.helper}
          </div>
        )}
        {/* 登出按钮只对 web 真实用户显示——桌面/本地（uid==="local"，合成账号）点登出会清掉
            authUser 把用户困在登录页（Codex review）。但今日额度对 local 同样适用（local 经
            managed 计费、默认 ¥5/天 cap、会被 reserve 拦），故额度行只要 daily_cap_yuan 是数字
            就显示（含 local），与登出门解耦。 */}
        {authUser && (authUser.uid !== 'local' || typeof authUser.daily_cap_yuan === 'number') && (
          <div className="mb-2 px-3 py-2 rounded-lg bg-[#15162d] border border-[#2f3158]">
            {authUser.uid !== 'local' && (
              <div className="flex items-center justify-between">
                <span className="text-xs text-[#e2e2f0] truncate">{authUser.username}</span>
                <button
                  onClick={async () => { try { await axios.post('/api/auth/logout') } catch (_) { /* ignore */ } onLoggedOut?.() }}
                  className="text-[11px] text-[#8888a8] hover:text-[#e2e2f0] ml-2"
                >登出</button>
              </div>
            )}
            {typeof authUser.daily_cap_yuan === 'number' && (() => {
              // 统一归一：quotaLabel / quotaRatio / overCap 都用同一组 finite 值，避免边界不一致
              // （daily_cap_yuan 虽是 number 但可能是 NaN，typeof 仍过——codex NIT）。
              const used = Number.isFinite(authUser.today_cost_yuan) ? authUser.today_cost_yuan : 0
              const cap = Number.isFinite(authUser.daily_cap_yuan) ? authUser.daily_cap_yuan : 0
              const ratio = quotaRatio(used, cap)
              // 触额度上限（含 cap<=0 被 admin 设 0 封禁）= 耗尽，统一红色 100%——否则
              // cap=0 时 quotaRatio 返 0 会显示 0% 青色，与「已被封/无额度」矛盾（codex NIT）。
              const overCap = cap <= 0 || used >= cap
              const pct = overCap ? 100 : Math.round(ratio * 100)
              // 配色随用量升级：<80% 主题青、≥80% 琥珀提醒、耗尽红。
              const barColor = overCap ? '#ef4444' : ratio >= 0.8 ? '#f5a623' : '#64ffda'
              return (
                <div className={authUser.uid !== 'local' ? 'mt-2' : ''}>
                  <div className="flex items-center justify-between text-[11px] mb-1">
                    <span className="text-gray-400">{quotaLabel(used, cap)}</span>
                    <span className="tabular-nums text-[#8f93c9]">{pct}%</span>
                  </div>
                  <div
                    className="h-1.5 w-full rounded-full bg-[#0f1024] overflow-hidden"
                    role="progressbar"
                    aria-label="今日额度使用进度"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={pct}
                  >
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${pct}%`, backgroundColor: barColor }}
                    />
                  </div>
                </div>
              )
            })()}
            {authUser?.is_admin && (
              <button
                onClick={() => onOpenAdmin?.()}
                className="text-[11px] text-[#64ffda] hover:underline mt-1"
              >
                👤 用户管理
              </button>
            )}
          </div>
        )}
        <button
          onClick={() => setShowSettings(true)}
          className="w-full text-[#8888a8] hover:text-[#e2e2f0] text-sm py-2 flex items-center justify-center gap-1"
        >
          ⚙ 连接设置
        </button>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onSaved={onSettingsSaved} />}

      {deleteConfirm && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-[#1a1a2e] rounded-lg p-6 w-96">
            <h2 className="text-lg font-semibold mb-4 text-[#e2e2f0]">确认删除</h2>
            <p className="text-[#8888a8] mb-6">确定要删除这个项目吗？此操作无法撤销。</p>
            <div className="flex gap-2">
              <button onClick={() => setDeleteConfirm(null)} className="flex-1 border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded hover:bg-[#222244]">取消</button>
              <button onClick={() => handleDelete(deleteConfirm)} className="flex-1 bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700">删除</button>
            </div>
          </div>
        </div>
      )}

      {showModal && <ProjectCreateModal onClose={() => setShowModal(false)} onCreate={onCreateProject} />}
    </div>
  )
}
