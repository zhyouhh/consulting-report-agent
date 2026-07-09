// 预览 img src 重写纯函数（图表 spec §4.5）。
import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveAssetSrc } from '../src/utils/assetUrl.js'

test('相对 assets 引用重写到项目资产路由', () => {
  assert.equal(
    resolveAssetSrc('assets/chart-abc123.png', 'pid-1'),
    '/api/projects/pid-1/assets/chart-abc123.png',
  )
})

test('./ 前缀与 query/fragment 都归一', () => {
  assert.equal(
    resolveAssetSrc('./assets/chart-a.png?v=2#x', 'pid-1'),
    '/api/projects/pid-1/assets/chart-a.png',
  )
})

test('projectId 与文件名做 URL 编码', () => {
  assert.equal(
    resolveAssetSrc('assets/chart a.png', 'p/1'),
    '/api/projects/p%2F1/assets/chart%20a.png',
  )
})

test('已编码文件名不双重编码', () => {
  assert.equal(
    resolveAssetSrc('assets/chart%20a.png', 'pid'),
    '/api/projects/pid/assets/chart%20a.png',
  )
})

test('绝对 URL / data: / 根相对 / 协议相对 原样返回', () => {
  for (const src of [
    'https://example.com/assets/x.png',
    'data:image/png;base64,AAA',
    '/assets/x.png',
    '//cdn.example.com/x.png',
  ]) {
    assert.equal(resolveAssetSrc(src, 'pid'), src)
  }
})

test('非 assets 相对路径与非 png 不重写', () => {
  assert.equal(resolveAssetSrc('images/x.png', 'pid'), 'images/x.png')
  assert.equal(resolveAssetSrc('assets/x.svg', 'pid'), 'assets/x.svg')
  assert.equal(resolveAssetSrc('assets/sub/x.png', 'pid'), 'assets/sub/x.png')
})

test('无 projectId 或非法 src 原样返回', () => {
  assert.equal(resolveAssetSrc('assets/x.png', null), 'assets/x.png')
  assert.equal(resolveAssetSrc(null, 'pid'), null)
  assert.equal(resolveAssetSrc('', 'pid'), '')
})
