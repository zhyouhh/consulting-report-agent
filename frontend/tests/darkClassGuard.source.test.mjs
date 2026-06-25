import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const SRC = fileURLToPath(new URL('../src/', import.meta.url))
const ALLOW_DARK = /dark:bg-scrim\/\d+/
function* walk(d){ for (const e of readdirSync(d,{withFileTypes:true})){const p=d+e.name
  if(e.isDirectory()){if(e.name!=='assets')yield* walk(p+'/')}else if(/\.jsx?$/.test(e.name))yield p}}
test('除遮罩外不得用 dark:（颜色靠 token 自动切，防真值源漂移）', () => {
  for (const p of walk(SRC)) {
    const src = readFileSync(p, 'utf8')
    const offenders = (src.match(/\bdark:[a-z0-9:/[\]#.-]+/g) || []).filter(s => !ALLOW_DARK.test(s))
    assert.deepEqual(offenders, [], `${p} 有非遮罩 dark: 类：${offenders.join(', ')}`)
  }
})
