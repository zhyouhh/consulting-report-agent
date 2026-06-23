import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const src = readFileSync(new URL('../src/api.js', import.meta.url), 'utf8')

// 全局 401 拦截器：只在未显式 skip 时才触发登出副作用——背景轮询（refreshAuthQuota）带
// skipUnauthedHandler:true，旧用户在途 /me 返 401 不该把当前（可能已是新登录）用户踢走（codex BLOCKER）。
test('api.js: 401 拦截器对 skipUnauthedHandler 请求跳过登出副作用', () => {
  assert.match(
    src,
    /status\s*===\s*401\s*&&\s*onUnauthed\s*&&\s*!error\.config\?\.skipUnauthedHandler/,
    '401 拦截器应在 onUnauthed 前排除 error.config.skipUnauthedHandler 的请求',
  )
  // 仍要把原始 error 透传给调用方（不被 handler 吞）。
  assert.match(src, /return Promise\.reject\(error\)/, '拦截器应继续 reject 原始 error')
})
