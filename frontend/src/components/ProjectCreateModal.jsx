import React, { useState } from 'react'

const initialForm = {
  name: '',
  project_type: 'strategy-consulting',
  theme: '',
  target_audience: '高层决策者',
  deadline: '',
  expected_length: '',
  notes: '',
}

export default function ProjectCreateModal({ onClose, onCreate }) {
  const [formData, setFormData] = useState(initialForm)
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
    if (!formData.name.trim()) {
      alert('请输入项目名称')
      return
    }
    if (!formData.theme.trim()) {
      alert('请输入报告主题')
      return
    }
    if (!formData.deadline.trim()) {
      alert('请输入截止日期')
      return
    }
    if (!formData.expected_length.trim()) {
      alert('请输入预期篇幅')
      return
    }

    setSaving(true)
    const success = await onCreate(formData)
    setSaving(false)

    if (success) {
      onClose()
    }
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-[#1a1a2e] rounded-lg p-6 w-[520px] border border-[#2f3158]">
        <h2 className="text-lg font-semibold mb-4 text-[#e2e2f0]">新建咨询项目</h2>

        <input
          placeholder="项目名称"
          value={formData.name}
          onChange={e => setFormData({ ...formData, name: e.target.value })}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        />

        <select
          value={formData.project_type}
          onChange={e => setFormData({ ...formData, project_type: e.target.value })}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        >
          <option value="strategy-consulting">战略咨询</option>
          <option value="market-research">市场研究</option>
          <option value="specialized-research">专项研究</option>
          <option value="management-document">管理制度</option>
          <option value="implementation-plan">实施方案</option>
          <option value="due-diligence">尽职调查</option>
        </select>

        <input
          placeholder="报告主题"
          value={formData.theme}
          onChange={e => setFormData({ ...formData, theme: e.target.value })}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        />

        <div className="grid grid-cols-2 gap-3 mb-3">
          <input
            placeholder="截止日期，例如 2026-04-01"
            value={formData.deadline}
            onChange={e => setFormData({ ...formData, deadline: e.target.value })}
            className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2"
          />
          <input
            placeholder="预期篇幅，例如 3000字"
            value={formData.expected_length}
            onChange={e => setFormData({ ...formData, expected_length: e.target.value })}
            className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2"
          />
        </div>

        <select
          value={formData.target_audience}
          onChange={e => setFormData({ ...formData, target_audience: e.target.value })}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        >
          <option value="高层决策者">高层决策者</option>
          <option value="中层管理者">中层管理者</option>
          <option value="执行团队">执行团队</option>
        </select>

        <textarea
          placeholder="已有材料或备注"
          value={formData.notes}
          onChange={e => setFormData({ ...formData, notes: e.target.value })}
          rows={4}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-4 resize-none"
        />

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded hover:bg-[#222244]">取消</button>
          <button onClick={handleCreate} disabled={saving} className="flex-1 bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:bg-[#3a3a5a]">
            {saving ? '创建中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
