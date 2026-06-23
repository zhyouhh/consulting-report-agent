import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/WorkspacePanel.jsx', import.meta.url), 'utf8')

const fn = src.slice(src.indexOf('const exportDraft'), src.indexOf('const exportDraft') + 900)

test('exportDraft 显式按 status !== "ok" 判失败并 showError', () => {
  assert.match(fn, /status\s*!==\s*['"]ok['"]/, '必须有 status !== "ok" 失败分支')
  assert.match(fn, /status\s*!==\s*['"]ok['"][\s\S]{0,120}showError/, '失败分支必须 showError 后 return')
})

test('exportDraft 成功后创建 anchor 触发下载 export-draft/download', () => {
  assert.match(fn, /createElement\(\s*['"]a['"]\s*\)/, '必须创建 <a> 触发下载')
  assert.match(fn, /export-draft\/download/, '下载 href 必须指向下载端点')
  assert.match(fn, /\.click\(\)/, '必须 .click() 触发下载')
})
