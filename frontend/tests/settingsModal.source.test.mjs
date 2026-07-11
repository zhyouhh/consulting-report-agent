import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('SettingsModal custom 模式输入仍在 + 不再提交 managed_base_url', () => {
  const s = readFileSync(new URL('../src/components/SettingsModal.jsx', import.meta.url), 'utf8')
  assert.match(s, /custom_api_base/)
  assert.match(s, /custom_api_key/)
  assert.doesNotMatch(s, /managed_base_url:/)   // 不再把 managed_base_url 放进提交体
})

test('SettingsModal 自定义搜索段：独立于模型模式 + provider 闭集 + key 掩码提示', () => {
  const s = readFileSync(new URL('../src/components/SettingsModal.jsx', import.meta.url), 'utf8')
  // 三字段进 form 与提交体
  assert.match(s, /custom_search_provider/)
  assert.match(s, /custom_search_api_key/)
  assert.match(s, /custom_search_api_base/)
  // provider 闭集下拉（与后端 _CUSTOM_SEARCH_PROVIDERS 同步）
  assert.match(s, /SEARCH_PROVIDER_OPTIONS/)
  for (const p of ['tavily', 'serper', 'brave', 'exa']) {
    assert.match(s, new RegExp(`value: '${p}'`))
  }
  // 独立性：搜索段渲染在 mode 三元分支之外（不被 form.mode === 'custom' 包裹）——
  // 锚定「自定义搜索（可选）」出现在 managed/custom 卡片闭合之后的独立卡片里
  assert.match(s, /自定义搜索（可选）/)
  assert.match(s, /相互独立/)
  // 选了渠道必须有 key；反向「有 key 无渠道」不再拦（Codex BLOCKER：掩码 key 会把
  // 想停用的用户困住——key 字段只在选了渠道时渲染，反向场景不可达）
  assert.match(s, /已选择搜索渠道，请填写对应的搜索 API Key/)
  assert.doesNotMatch(s, /已填写搜索 API Key，请选择搜索渠道/)
})

test('SettingsModal 掩码 key 三修复（Codex 红队 BLOCKER）', () => {
  const s = readFileSync(new URL('../src/components/SettingsModal.jsx', import.meta.url), 'utf8')
  // 切渠道清 key / 切回载入渠道恢复掩码——旧渠道的 key 不能带给新渠道
  assert.match(s, /handleSearchProviderChange/)
  assert.match(s, /value === initialSearch\.provider \? initialSearch\.key : ''/)
  // 停用（渠道空）时提交掩码保留服务端已存 key，不清、不拦
  assert.match(s, /\{ \.\.\.form, custom_search_api_key: '\*\*\*' \}/)
})
