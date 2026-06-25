import React, { useEffect, useState } from 'react'
import axios from 'axios'

// 默认代理地址仅作只读展示——服务端只读、提交体不再发送 managed_base_url（B3 Task 18）。
const MANAGED_BASE_URL_DISPLAY = 'https://newapi.z0y0h.work/client/v1'

const defaultForm = {
  mode: 'managed',
  managed_model: 'deepseek-v4-pro',
  custom_api_base: '',
  custom_api_key: '',
  custom_model: '',
  custom_context_limit_override: null,
}

export default function SettingsModal({ onClose, onSaved }) {
  const [form, setForm] = useState(defaultForm)
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [models, setModels] = useState([])
  const [fetchingModels, setFetchingModels] = useState(false)

  useEffect(() => {
    axios.get('/api/settings').then(res => {
      setForm({
        mode: res.data.mode || 'managed',
        managed_model: res.data.managed_model || defaultForm.managed_model,
        custom_api_base: res.data.custom_api_base || '',
        custom_api_key: res.data.custom_api_key || '',
        custom_model: res.data.custom_model || '',
        custom_context_limit_override: res.data.custom_context_limit_override ?? null,
      })
      setLoaded(true)
    }).catch(() => setLoaded(true))
  }, [])

  useEffect(() => {
    setModels([])
  }, [form.custom_api_base, form.custom_api_key])

  const fetchModels = async () => {
    if (!form.custom_api_key.trim() || form.custom_api_key === '***') {
      alert('请先输入自定义 API Key')
      return
    }
    if (!form.custom_api_base.trim()) {
      alert('请先输入自定义 API 地址')
      return
    }
    setFetchingModels(true)
    try {
      const res = await axios.post('/api/models/list', {
        api_key: form.custom_api_key,
        api_base: form.custom_api_base,
      })
      setModels(res.data.models)
      if (res.data.models.length > 0 && !res.data.models.includes(form.custom_model)) {
        setForm(prev => ({ ...prev, custom_model: res.data.models[0] }))
      }
    } catch (e) {
      alert('获取模型列表失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setFetchingModels(false)
    }
  }

  const handleSave = async () => {
    if (form.mode === 'custom') {
      if (!form.custom_api_base.trim()) {
        alert('请输入自定义 API 地址')
        return
      }
      if (!form.custom_api_key.trim()) {
        alert('请输入自定义 API Key')
        return
      }
      if (!form.custom_model.trim()) {
        alert('请输入自定义模型')
        return
      }
    }

    setSaving(true)
    try {
      await axios.post('/api/settings', form)
      if (onSaved) {
        await onSaved()
      }
      onClose()
    } catch (e) {
      alert('保存失败: ' + (e.response?.data?.detail || e.message))
    } finally {
      setSaving(false)
    }
  }

  if (!loaded) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60">
      <div className="bg-card rounded-win p-6 w-[560px] border border-border shadow-popover">
        <h2 className="text-lg font-semibold mb-1 text-text">连接设置</h2>
        <p className="text-sm text-t2 mb-5">试用通道仅供快速试用，无服务质量承诺，推荐有条件使用自定义 API。</p>

        <div className="grid grid-cols-2 gap-3 mb-5">
          <button
            onClick={() => setForm(prev => ({ ...prev, mode: 'managed' }))}
            className={`text-left rounded-card p-4 border transition ${
              form.mode === 'managed'
                ? 'border-accent bg-asoft'
                : 'border-border bg-card2 hover:bg-asoft'
            }`}
          >
            <div className="text-sm font-semibold text-text mb-1">试用通道</div>
            <div className="text-xs text-t2">仅供快速试用</div>
            <div className="text-xs text-abright mt-2">{form.managed_model}</div>
          </button>
          <button
            onClick={() => setForm(prev => ({ ...prev, mode: 'custom' }))}
            className={`text-left rounded-card p-4 border transition ${
              form.mode === 'custom'
                ? 'border-accent bg-asoft'
                : 'border-border bg-card2 hover:bg-asoft'
            }`}
          >
            <div className="text-sm font-semibold text-text mb-1">自定义 API</div>
            <div className="text-xs text-t2">高级配置，自行承担可用性</div>
            <div className="text-xs text-abright mt-2">{form.custom_model || '未选择模型'}</div>
          </button>
        </div>

        <div className="rounded-card border border-border bg-card2 p-4 mb-5">
          {form.mode === 'managed' ? (
            <>
              <label className="block text-sm text-t2 mb-1">默认代理地址</label>
              <input
                value={MANAGED_BASE_URL_DISPLAY}
                readOnly
                className="w-full bg-field border border-border text-t2 rounded-btn px-3 py-2 mb-3"
              />

              <label className="block text-sm text-t2 mb-1">默认模型</label>
              <input
                value={form.managed_model}
                readOnly
                className="w-full bg-field border border-border text-t2 rounded-btn px-3 py-2"
              />
            </>
          ) : (
            <>
              <label className="block text-sm text-t2 mb-1">自定义 API Key</label>
              <input
                type="password"
                placeholder="输入你的 API Key"
                value={form.custom_api_key}
                onChange={e => setForm(prev => ({ ...prev, custom_api_key: e.target.value }))}
                className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-accent"
              />

              <label className="block text-sm text-t2 mb-1">自定义 API 地址</label>
              <input
                value={form.custom_api_base}
                onChange={e => setForm(prev => ({ ...prev, custom_api_base: e.target.value }))}
                className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-accent"
              />

              <label className="block text-sm text-t2 mb-1">模型</label>
              <div className="flex gap-2 mb-2">
                {models.length > 0 ? (
                  <select
                    value={form.custom_model}
                    onChange={e => setForm(prev => ({ ...prev, custom_model: e.target.value }))}
                    className="flex-1 bg-field border border-border text-text rounded-btn px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
                  >
                    {models.map(model => <option key={model} value={model}>{model}</option>)}
                  </select>
                ) : (
                  <input
                    value={form.custom_model}
                    onChange={e => setForm(prev => ({ ...prev, custom_model: e.target.value }))}
                    placeholder="例如: gpt-4.1-mini"
                    className="flex-1 bg-field border border-border text-text rounded-btn px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
                  />
                )}
                <button
                  onClick={fetchModels}
                  disabled={fetchingModels}
                  className="bg-card2 border border-border text-text px-4 py-2 rounded-btn hover:bg-asoft disabled:opacity-60"
                >
                  {fetchingModels ? '获取中...' : '获取模型'}
                </button>
              </div>
              <label className="block text-sm text-t2 mb-1">有效上下文上限（高级）</label>
              <input
                type="number"
                min="4096"
                step="1000"
                value={form.custom_context_limit_override ?? ''}
                onChange={e => setForm(prev => ({
                  ...prev,
                  custom_context_limit_override: e.target.value ? Number(e.target.value) : null,
                }))}
                placeholder="留空按模型自动识别"
                className="w-full bg-field border border-border text-text rounded-btn px-3 py-2 mb-2 focus:outline-none focus:ring-2 focus:ring-accent"
              />
              <p className="text-xs text-t3 mb-3">留空表示自动识别；填写后会覆盖客户端采用的有效上下文上限。</p>
              <p className="text-xs text-t3">支持 OpenAI 兼容接口。若 API Key 显示为 `***`，直接保存会保留原值。</p>
            </>
          )}
        </div>

        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 border border-border text-text px-4 py-2 rounded-btn hover:bg-card2">
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 bg-accent text-white px-4 py-2 rounded-btn hover:bg-accent/90 disabled:opacity-60"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
