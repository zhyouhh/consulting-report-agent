import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reduceAuth, isAuthError } from '../src/utils/authState.js'

test('reduceAuth', () => {
  assert.equal(reduceAuth({ status: 'loading' }, { type: 'authed', user: { username: 'a' } }).status, 'authed')
  assert.equal(reduceAuth({ status: 'authed' }, { type: 'unauthed' }).status, 'login')
})
test('isAuthError', () => {
  assert.equal(isAuthError({ response: { status: 401 } }), true)
  assert.equal(isAuthError({ response: { status: 500 } }), false)
})
