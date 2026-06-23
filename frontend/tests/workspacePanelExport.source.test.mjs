import { test } from 'node:test'
import assert from 'node:assert'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/components/WorkspacePanel.jsx', import.meta.url), 'utf8')

const fn = src.slice(src.indexOf('const exportDraft'), src.indexOf('const exportDraft') + 900)

test('exportDraft 显式按 status !== "ok" 判失败并 showError 后 return（在触发下载之前）', () => {
  assert.match(fn, /status\s*!==\s*['"]ok['"]/, '必须有 status !== "ok" 失败分支')
  assert.match(fn, /status\s*!==\s*['"]ok['"][\s\S]{0,120}showError/, '失败分支必须先 showError')
  // 锁住失败短路：showError 之后必须 return，且 return 在创建下载 anchor 之前——
  // 否则回归删掉 return 会在报错后仍触发下载 + 成功 toast（codex R2 Minor）。
  const errIdx = fn.search(/showError/)
  const retIdx = fn.indexOf('return', errIdx)
  const anchorIdx = fn.search(/createElement\(\s*['"]a['"]\s*\)/)
  assert.ok(retIdx > -1, 'showError 后必须有 return')
  assert.ok(anchorIdx > -1, '必须有下载 anchor')
  assert.ok(retIdx < anchorIdx, '失败分支的 return 必须在创建下载 anchor 之前')
})

test('exportDraft 成功后创建 anchor 触发下载 export-draft/download', () => {
  assert.match(fn, /createElement\(\s*['"]a['"]\s*\)/, '必须创建 <a> 触发下载')
  assert.match(fn, /export-draft\/download/, '下载 href 必须指向下载端点')
  assert.match(fn, /\.click\(\)/, '必须 .click() 触发下载')
})
