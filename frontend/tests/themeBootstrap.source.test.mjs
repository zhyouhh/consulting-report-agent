import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
test('bootstrap 在 head 内、排在 module script 之前', () => {
  const headEnd = html.indexOf('</head>')
  const boot = html.indexOf('cra:theme')
  const mod = html.search(/<script[^>]+type=["']module["']/)
  assert.ok(boot !== -1 && boot < headEnd, 'bootstrap 应在 </head> 之前')
  assert.ok(mod === -1 || boot < mod, 'bootstrap 应排在 module script 之前')
})
test('bootstrap 语义：try/catch + 只 dark 加 .dark + 非 dark 移除', () => {
  assert.match(html, /try\s*\{[\s\S]*cra:theme[\s\S]*===\s*['"]dark['"][\s\S]*classList\.add\(['"]dark['"]\)[\s\S]*\}\s*catch/)
  assert.match(html, /classList\.remove\(['"]dark['"]\)/)
})
