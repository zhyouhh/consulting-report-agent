import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const cfg = readFileSync(new URL('../tailwind.config.js', import.meta.url), 'utf8')
const EXPECTED_TOKENS = ['--bg','--chat','--ws','--card','--card2','--field','--border','--col',
  '--hair','--track','--text','--t2','--t3','--accent','--abright','--asoft','--asoftb','--asoftt',
  '--sel','--userbub','--stepdone','--dotfuture','--scrim','--success','--warn','--error']
test('helper 用 <alpha-value> 通道形式 + darkMode class', () => {
  assert.match(cfg, /rgb\(var\(\$\{v\}\) \/ <alpha-value>\)/)
  assert.match(cfg, /darkMode:\s*['"]class['"]/)
})
test('每个 color token 都经 c() helper（无裸 var(--x) 丢透明度）', () => {
  for (const t of EXPECTED_TOKENS) assert.match(cfg, new RegExp(`c\\(['"]${t}['"]\\)`), `${t} 未经 c()`)
  assert.ok(!/:\s*['"]var\(--[a-z0-9-]+\)['"]/.test(cfg), '存在裸 var(--x) 颜色映射、丢 <alpha-value>')
})
