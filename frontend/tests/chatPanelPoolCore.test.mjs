import test from 'node:test'
import assert from 'node:assert/strict'

import {
  NO_PROJECT_SENTINEL,
  createChatPanelPoolCore,
  runLogout,
  runTwoPassShutdown,
  shouldContinueAfterUpload,
} from '../src/utils/chatPanelPoolCore.js'

test('mounted projects are unique and sentinel never enters membership', () => {
  const core = createChatPanelPoolCore()
  assert.deepEqual(core.computeMounted(null), [NO_PROJECT_SENTINEL])
  core.computeMounted('a')
  core.computeMounted('b')
  core.computeMounted('a')
  assert.deepEqual(core.snapshot().members, ['a', 'b'])
  assert.deepEqual(core.computeMounted(null), ['a', 'b', NO_PROJECT_SENTINEL])
})

test('stream admission is synchronous, token-bound, and deleting has priority', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 2 })
  core.visitProject('a'); core.visitProject('b'); core.visitProject('c')
  const a = core.acquireStreamLease('a')
  assert.equal(a.status, 'started')
  assert.equal(core.acquireStreamLease('a').status, 'same_pid')
  const b = core.acquireStreamLease('b')
  assert.equal(core.acquireStreamLease('c').status, 'cap_full')
  assert.equal(core.releaseStreamLease('bad-token'), false)
  assert.equal(core.releaseStreamLease(a.token), true)
  const deleting = core.tryBeginDelete('a')
  assert.equal(deleting.status, 'started')
  assert.equal(core.acquireStreamLease('a').status, 'deleting')
  assert.equal(core.releaseStreamLease(b.token), true)
  assert.equal(core.acquireStreamLease(NO_PROJECT_SENTINEL).status, 'missing')
})

test('deleting wins over same-pid and cap-full admission states', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 2 })
  ;['a', 'b', 'c'].forEach(pid => core.visitProject(pid))
  const a = core.acquireStreamLease('a')
  const b = core.acquireStreamLease('b')
  assert.equal(core.tryBeginDelete('a').status, 'started')
  assert.equal(core.tryBeginDelete('c').status, 'started')
  assert.equal(core.acquireStreamLease('a').status, 'deleting', 'delete must beat same_pid')
  assert.equal(core.acquireStreamLease('c').status, 'deleting', 'delete must beat cap_full')
  assert.equal(core.releaseStreamLease(a.token), true)
  assert.equal(core.releaseStreamLease(a.token), false, 'lease token is exactly once')
  assert.equal(core.releaseStreamLease(b.token), true)
})

test('waiter drain preserves FIFO and handles local busy without spinning', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 1 })
  core.visitProject('a'); core.visitProject('b'); core.visitProject('c')
  const lease = core.acquireStreamLease('a')
  const calls = []
  core.registerPermitFlusher('b', () => { calls.push('b'); return { status: 'local_busy' } })
  core.registerPermitFlusher('c', () => { calls.push('c'); return core.acquireStreamLease('c') })
  core.enqueuePermitWaiter('b'); core.enqueuePermitWaiter('c'); core.enqueuePermitWaiter('b')
  core.releaseStreamLease(lease.token)
  assert.deepEqual(calls, ['b', 'c'])
  assert.deepEqual(core.snapshot().leases, ['c'])
  assert.deepEqual(core.snapshot().waiters, [])
})

test('cap-full waiter is requeued and later starts after a release', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 1 })
  core.visitProject('a'); core.visitProject('b')
  const a = core.acquireStreamLease('a')
  core.registerPermitFlusher('b', () => core.acquireStreamLease('b'))
  core.enqueuePermitWaiter('b')
  assert.equal(core.drainPermitWaiters().status, 'cap_full')
  assert.deepEqual(core.snapshot().waiters, ['b'])
  core.releaseStreamLease(a.token)
  assert.deepEqual(core.snapshot().leases, ['b'])
})

test('waiter removed on local upload busy and can be re-added by the upload wake-up', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 1 })
  core.visitProject('a'); core.visitProject('d')
  const a = core.acquireStreamLease('a')
  const upload = core.beginUpload('d')
  let uploading = true
  core.registerPermitFlusher('d', () => (
    uploading ? { status: 'local_busy' } : core.acquireStreamLease('d')
  ))
  core.enqueuePermitWaiter('d')
  core.releaseStreamLease(a.token)
  assert.deepEqual(core.snapshot().waiters, [], 'global drain must not spin/requeue local busy')
  uploading = false
  core.endUpload(upload)
  core.enqueuePermitWaiter('d')
  assert.equal(core.drainPermitWaiters().status, 'started')
  assert.deepEqual(core.snapshot().leases, ['d'])
})

test('waiter classification drops deleting, empty, stale and missing entries', () => {
  const core = createChatPanelPoolCore({ maxConcurrentStreams: 1 })
  ;['a', 'b', 'c', 'd'].forEach(pid => core.visitProject(pid))
  const a = core.acquireStreamLease('a')
  core.registerPermitFlusher('b', () => ({ status: 'empty' }))
  core.registerPermitFlusher('c', () => ({ status: 'stale' }))
  core.registerPermitFlusher('d', () => ({ status: 'deleting' }))
  core.enqueuePermitWaiter('b'); core.enqueuePermitWaiter('c'); core.enqueuePermitWaiter('d')
  core.releaseStreamLease(a.token)
  assert.deepEqual(core.snapshot().waiters, [])
  assert.deepEqual(core.snapshot().leases, [])
})

test('multiple upload producers block delete until every token ends', () => {
  const core = createChatPanelPoolCore()
  core.visitProject('a')
  const one = core.beginUpload('a')
  const two = core.beginUpload('a')
  assert.equal(core.tryBeginDelete('a').status, 'uploading')
  core.endUpload(one)
  assert.equal(core.tryBeginDelete('a').status, 'uploading')
  core.endUpload(two)
  const deletion = core.tryBeginDelete('a')
  assert.equal(deletion.status, 'started')
  assert.equal(core.beginUpload('a'), null)
  assert.equal(core.tryBeginDelete('a').status, 'deleting')
  assert.equal(core.finishDelete('stale', { forgotten: false }), 'stale')
  assert.equal(core.finishDelete('stale', { forgotten: true }), 'stale')
  assert.equal(core.finishDelete(deletion.token, { forgotten: false }), 'resume_required')
  const resumed = core.acquireStreamLease('a')
  assert.equal(resumed.status, 'started', 'failed deletion must synchronously reopen stream admission')
  assert.equal(core.releaseStreamLease(resumed.token), true)
  assert.equal(core.finishDelete(deletion.token, { forgotten: false }), 'stale')
})

test('a valid unvisited project can be deleted directly from the sidebar', () => {
  const core = createChatPanelPoolCore()
  const deletion = core.tryBeginDelete('never-opened')
  assert.equal(deletion.status, 'started')
  assert.equal(core.acquireStreamLease('never-opened').status, 'deleting')
  assert.equal(core.beginUpload('never-opened'), null)
  assert.equal(core.finishDelete(deletion.token, { forgotten: true }), 'forgotten')
  assert.deepEqual(core.snapshot(), { members: [], leases: [], waiters: [], uploads: [], deleting: [] })
})

test('forgotten delete atomically clears every project table', () => {
  const core = createChatPanelPoolCore()
  core.visitProject('a')
  const lease = core.acquireStreamLease('a')
  core.enqueuePermitWaiter('a')
  const deletion = core.tryBeginDelete('a')
  // tryBeginDelete is allowed during a stream; frontend aborts it before DELETE.
  assert.equal(deletion.status, 'started')
  assert.equal(core.finishDelete(deletion.token, { forgotten: true }), 'forgotten')
  assert.deepEqual(core.snapshot(), { members: [], leases: [], waiters: [], uploads: [], deleting: [] })
  assert.equal(core.releaseStreamLease(lease.token), false)
  assert.equal(core.acquireStreamLease('a').status, 'missing', 'forgotten handle cannot create a ghost lease')
  assert.equal(core.beginUpload('a'), null, 'forgotten handle cannot create a ghost upload')
})

test('shutdown helpers are fail-closed and ordered', async () => {
  assert.equal(shouldContinueAfterUpload({ mounted: true, accepting: true }), true)
  assert.equal(shouldContinueAfterUpload({ mounted: false, accepting: true }), false)
  const events = []
  const handles = ['a', 'b'].map(id => ({
    stopAcceptingWork: () => events.push(`stop-${id}`),
    abortActiveStream: () => events.push(`abort-${id}`),
    cancelActiveUpload: () => events.push(`upload-${id}`),
    cancelPendingWork: () => events.push(`pending-${id}`),
  }))
  runTwoPassShutdown(handles)
  assert.deepEqual(events, [
    'stop-a', 'stop-b',
    'abort-a', 'upload-a', 'pending-a',
    'abort-b', 'upload-b', 'pending-b',
  ])

  let cleared = false
  await assert.rejects(() => runLogout({
    abortAll: () => events.push('abort-all'),
    requestLogout: async () => { events.push('request-logout'); throw new Error('network') },
    clearSession: () => { events.push('clear-session'); cleared = true },
  }))
  assert.equal(cleared, true)
  assert.deepEqual(events.slice(-3), ['abort-all', 'request-logout', 'clear-session'])
})
