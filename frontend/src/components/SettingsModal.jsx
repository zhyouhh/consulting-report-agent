import React, { useState, useEffect } from 'react'
import axios from 'axios'

export default function SettingsModal({ onClose }) {
  const [form, setForm] = useState({
    api_key: '',
    api_base: 'https://api.siliconflow.cn/v1',
    model: 'deepseek-ai/DeepSeek-V3',
  })
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    axios.get('/api/settings').then(res => {
      setForm({
        api_key: res.data.api_key === '***' ? '' : res.data.api_key,
        api_base: res.data.api_base || 'https://api.siliconflow.cn/v1',
        model: res.data.model || 'deepseek-ai/DeepSeek-V3',
      })
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  const handleSave = async () => {
    if (!form.api_key.trim()) {
      alert('请输入API Key')
      return
    }
    setSaving(true)
    try {
      await axios.post('/api/settings', {
        api_provider: 'siliconflow',
        api_key: form.api_key,
        api_base: form.api_base,
        model: form.model,
      })
      alert('保存成功')
      onClose()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) return null

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-[#1a1a2e] rounded-lg p-6 w-[420px]">
        <h2 className="text-lg font-semibold mb-4 text-[#e2e2f0]">API 设置</h2>

        <label className="block text-sm text-[#8888a8] mb-1">API Key</label>
        <input
          type="password"
          placeholder="输入你的API Key"
          value={form.api_key}
          onChange={e => setForm({...form, api_key: e.target.value})}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        />

        <label className="block text-sm text-[#8888a8] mb-1">API 地址</label>
        <input
          value={form.api_base}
          onChange={e => setForm({...form, api_base: e.target.value})}
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-3"
        />

        <label className="block text-sm text-[#8888a8] mb-1">模型</label>
        <input
          value={form.model}
          onChange={e => setForm({...form, model: e.target.value})}
          placeholder="例如: deepseek-ai/DeepSeek-V3"
          className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-1"
        />
        <p className="text-xs text-[#6a6a8a] mb-4">
          硅基流动常用模型: deepseek-ai/DeepSeek-V3, Qwen/Qwen2.5-72B-Instruct
        </p>

        <p className="text-xs text-[#6a6a8a] mb-4">
          支持硅基流动API及其他兼容OpenAI格式的服务
        </p>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 border border-[#3a3a5a] text-[#e2e2f0] px-4 py-2 rounded hover:bg-[#222244]">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700 disabled:bg-[#3a3a5a]"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
