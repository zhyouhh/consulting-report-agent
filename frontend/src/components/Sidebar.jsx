import React, { useState } from 'react'

export default function Sidebar({ projects, currentProject, onSelectProject, onCreateProject }) {
  const [showModal, setShowModal] = useState(false)
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

  return (
    <div className="w-64 bg-white border-r border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-200">
        <h1 className="text-lg font-semibold text-gray-800">咨询报告助手</h1>
      </div>

      <div className="p-4">
        <button
          onClick={() => setShowModal(true)}
          className="w-full bg-blue-500 text-white px-4 py-2 rounded-lg hover:bg-blue-600"
        >
          + 新建报告
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4">
        {projects.map((project) => (
          <div
            key={project.name}
            onClick={() => onSelectProject(project.name)}
            className={`p-3 mb-2 rounded-lg cursor-pointer ${
              currentProject === project.name ? 'bg-blue-50 border border-blue-200' : 'hover:bg-gray-50'
            }`}
          >
            <div className="font-medium text-sm text-gray-800">{project.name}</div>
          </div>
        ))}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-96">
            <h2 className="text-lg font-semibold mb-4">新建报告项目</h2>
            <input
              placeholder="项目名称"
              value={formData.name}
              onChange={e => setFormData({...formData, name: e.target.value})}
              className="w-full border border-gray-300 rounded px-3 py-2 mb-3"
            />
            <select
              value={formData.report_type}
              onChange={e => setFormData({...formData, report_type: e.target.value})}
              className="w-full border border-gray-300 rounded px-3 py-2 mb-3"
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
              className="w-full border border-gray-300 rounded px-3 py-2 mb-3"
            />
            <select
              value={formData.target_audience}
              onChange={e => setFormData({...formData, target_audience: e.target.value})}
              className="w-full border border-gray-300 rounded px-3 py-2 mb-4"
            >
              <option value="高层决策者">高层决策者</option>
              <option value="中层管理者">中层管理者</option>
              <option value="执行团队">执行团队</option>
            </select>
            <div className="flex gap-2">
              <button onClick={() => setShowModal(false)} className="flex-1 border border-gray-300 px-4 py-2 rounded">取消</button>
              <button onClick={handleCreate} className="flex-1 bg-blue-500 text-white px-4 py-2 rounded">创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
