import React, { useState } from 'react'
import { prepareProjectCreatePayload } from '../utils/projectCreatePayload'

const initialForm = {
  project_type: 'strategy-consulting',
  theme: '',
  deadline: '',
  expected_length: '',
}

export default function ProjectCreateModal({ onClose, onCreate }) {
  const [formData, setFormData] = useState(initialForm)
  const [saving, setSaving] = useState(false)

  const handleCreate = async () => {
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
    let success = false
    try {
      success = await onCreate(prepareProjectCreatePayload(formData))
    } catch (error) {
      alert(error instanceof Error ? error.message : '请输入有效的报告主题')
    }
    setSaving(false)

    if (success) {
      onClose()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60">
      <div className="bg-card rounded-win p-6 w-[560px] border border-border shadow-popover">
        <select
          value={formData.project_type}
          onChange={e => setFormData({ ...formData, project_type: e.target.value })}
          className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-accent"
        >
          <option value="strategy-consulting">战略咨询</option>
          <option value="market-research">市场研究</option>
          <option value="specialized-research">专项研究</option>
          <option value="management-document">管理制度</option>
          <option value="implementation-plan">实施方案</option>
          <option value="due-diligence">尽职调查</option>
          <option value="technical-bid">技术标（投标）</option>
        </select>

        <input
          placeholder="报告主题"
          value={formData.theme}
          onChange={e => setFormData({ ...formData, theme: e.target.value })}
          className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-accent"
        />

        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <div className="text-sm text-t2 mb-2">截止日期</div>
            <input
              type="date"
              value={formData.deadline}
              onChange={e => setFormData({ ...formData, deadline: e.target.value })}
              className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          <div>
            <div className="text-sm text-t2 mb-2">预期篇幅</div>
            <input
              placeholder="例如 3000字"
              value={formData.expected_length}
              onChange={e => setFormData({ ...formData, expected_length: e.target.value })}
              className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 border border-border text-text px-4 py-2 rounded-btn hover:bg-card2">取消</button>
          <button onClick={handleCreate} disabled={saving} className="flex-1 bg-accent text-white px-4 py-2 rounded-btn hover:bg-accent/90 disabled:opacity-60">
            {saving ? '创建中...' : '创建'}
          </button>
        </div>
      </div>
    </div>
  )
}
