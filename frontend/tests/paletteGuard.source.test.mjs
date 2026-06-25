import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const SRC = fileURLToPath(new URL('../src/', import.meta.url))
const ARB = /\b(?:bg|text|border|ring|placeholder|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]/
const HEX = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b/
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u
// 起始放全部仍含旧色的文件名：17 个组件 .jsx + 'App.jsx' + 'toast.js'（index.css 已在 0b 迁移、不入）；每 task 完成后移除对应文件，迁移完为空
const ALLOW_PENDING = new Set([
  'App.jsx','toast.js',
  'AdminPanel.jsx','ChatPanel.jsx','ConfirmDialog.jsx','ErrorBoundary.jsx','FilePreviewPanel.jsx',
  'ForcePasswordChange.jsx','IndependentReviewDrawer.jsx','Login.jsx','MarkdownMessage.jsx',
  'ProjectCreateModal.jsx','RollbackMenu.jsx','SettingsModal.jsx','Sidebar.jsx',
  'StageAdvanceControl.jsx','StagePanel.jsx','ThinkingBlock.jsx','WorkspacePanel.jsx',
])
function* walk(d){ for (const e of readdirSync(d, {withFileTypes:true})) {
  const p = d + e.name; if (e.isDirectory()) { if (e.name!=='assets') yield* walk(p+'/') }
  else if (/\.(jsx?|css)$/.test(e.name)) yield { name:e.name, path:p } } }
test('全 src 无遗留旧 palette / 裸 hex / emoji（迁移完 ALLOW_PENDING 空）', () => {
  for (const { name, path } of walk(SRC)) {
    if (ALLOW_PENDING.has(name)) continue
    const src = readFileSync(path, 'utf8')
    assert.ok(!ARB.test(src), `${name} 仍有任意值颜色类`)
    assert.ok(!HEX.test(src), `${name} 仍有裸 hex 色值（token 用 RGB channel、shadow 用 rgba）`)
    if (/\.jsx$/.test(name)) assert.ok(!EMOJI.test(src), `${name} 仍有 emoji`)
  }
})
