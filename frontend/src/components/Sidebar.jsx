import React, { useState } from 'react'
import SettingsModal from './SettingsModal'
import { describeConnectionMode } from '../utils/connectionMode'

export default function Sidebar({
  projects,
  currentProject,
  settings,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onSettingsSaved,
}) {
  const [showModal, setShowModal] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [formData, setFormData] = useState({
    name: '',
    report_type: 'research-report',
    theme: '',
    target_audience: '高层决策者'
  })

  const handleCreate = async () => {
    if (!formData.name.trim()) {
      alert('请输入项目名称')
      return
    }
    if (!formData.theme.trim()) {
      alert('请输入报告主题')
      return
    }
    const success = await onCreateProject(formData)
    if (success) {
      setShowModal(false)
      setFormData({ name: '', report_type: 'research-report', theme: '', target_audience: '高层决策者' })
    }
  }

  const handleDelete = async (projectName) => {
    const success = await onDeleteProject(projectName)
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
            key={project.name}
            className={`p-3 mb-2 rounded-lg flex items-center justify-between ${
              currentProject === project.name ? 'bg-[#1e1e4a] border border-[#3b5998]' : 'hover:bg-[#222244]'
            }`}
          >
            <div
              onClick={() => onSelectProject(project.name)}
              className="flex-1 cursor-pointer font-medium text-sm text-[#e2e2f0]"
            >
              {project.name}
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                setDeleteConfirm(project.name)
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
            <p className="text-[#8888a8] mb-6">确定要删除项目 "{deleteConfirm}" 吗？此操作无法撤销。</p>
            <div className="flex gap-2">
              <button onClick={() => setDeleteConfirm(null)} className="flex-1 border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded hover:bg-[#222244]">取消</button>
              <button onClick={() => handleDelete(deleteConfirm)} className="flex-1 bg-red-600 text-white px-4 py-2 rounded hover:bg-red-700">删除</button>
            </div>
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-[#1a1a2e] rounded-lg p-6 w-96">
            <h2 className="text-lg font-semibold mb-4 text-[#e2e2f0]">新建报告项目</h2>
            <input
              placeholder="项目名称"
              value={formData.name}
              onChange={e => setFormData({...formData, name: e.target.value})}
              className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
            />
            <select
              value={formData.report_type}
              onChange={e => setFormData({...formData, report_type: e.target.value})}
              className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
            >
              <option value="research-report">专题研究报告</option>
              <option value="system-plan">体系规划方案</option>
              <option value="implementation">实施工作方案</option>
              <option value="regulation">管理制度</option>
            </select>
            <input
              placeholder="报告主题"
              value={formData.theme}
              onChange={e => setFormData({...formData, theme: e.target.value})}
              className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
            />
            <select
              value={formData.target_audience}
              onChange={e => setFormData({...formData, target_audience: e.target.value})}
              className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-4"
            >
              <option value="高层决策者">高层决策者</option>
              <option value="中层管理者">中层管理者</option>
              <option value="执行团队">执行团队</option>
            </select>
            <div className="flex gap-2">
              <button onClick={() => setShowModal(false)} className="flex-1 border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded hover:bg-[#222244]">取消</button>
              <button onClick={handleCreate} className="flex-1 bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
