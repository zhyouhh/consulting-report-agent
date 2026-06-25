import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeTheme, nextTheme, getInitialTheme, applyTheme, toggleTheme } from '../src/utils/theme.js'

beforeEach(() => {
  const store = {}
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
  }
  const classes = new Set()
  globalThis.document = { documentElement: { classList: {
    add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
  } } }
})

test('normalizeTheme: 只认 dark，其余回 light', () => {
  assert.equal(normalizeTheme('dark'), 'dark')
  assert.equal(normalizeTheme('light'), 'light')
  assert.equal(normalizeTheme(null), 'light')
  assert.equal(normalizeTheme('DARK'), 'light')
  assert.equal(normalizeTheme(undefined), 'light')
})
test('nextTheme 翻转', () => {
  assert.equal(nextTheme('light'), 'dark')
  assert.equal(nextTheme('dark'), 'light')
})
test('getInitialTheme 读 localStorage、缺省 light、异常吞掉', () => {
  assert.equal(getInitialTheme(), 'light')
  globalThis.localStorage.setItem('cra:theme', 'dark')
  assert.equal(getInitialTheme(), 'dark')
  globalThis.localStorage.getItem = () => { throw new Error('blocked') }
  assert.equal(getInitialTheme(), 'light')
})
test('applyTheme 加/去 .dark 并返回归一值', () => {
  assert.equal(applyTheme('dark'), 'dark')
  assert.ok(globalThis.document.documentElement.classList.contains('dark'))
  assert.equal(applyTheme('light'), 'light')
  assert.ok(!globalThis.document.documentElement.classList.contains('dark'))
  applyTheme('garbage')
  assert.ok(!globalThis.document.documentElement.classList.contains('dark'))
})
test('toggleTheme 翻转 + 持久化 + 应用', () => {
  const t = toggleTheme('light')
  assert.equal(t, 'dark')
  assert.equal(globalThis.localStorage.getItem('cra:theme'), 'dark')
  assert.ok(globalThis.document.documentElement.classList.contains('dark'))
})
