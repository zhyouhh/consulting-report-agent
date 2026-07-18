import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (name) => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8')
const pool = read('components/ChatPanelPool.jsx')
const chat = read('components/ChatPanel.jsx')
const app = read('App.jsx')
const workspace = read('components/WorkspacePanel.jsx')

test('ChatPanelPool is a thin persistent shell with sentinel and layout-transparent visibility', () => {
  assert.match(pool, /createChatPanelPoolCore/)
  assert.match(pool, /NO_PROJECT_SENTINEL/)
  assert.match(pool, /snapshot\.members/)
  assert.match(pool, /display: visible \? 'contents' : 'none'/)
  assert.match(pool, /useLayoutEffect\(\(\) => \{\s*if \(activeProjectId\) core\.visitProject\(activeProjectId\)/)
  assert.match(pool, /pendingAutoStartProjectIds\?\.has\?\.\(projectId\)/)
  assert.match(pool, /onPanelMaterialsMerged\?\.\(projectId, materials\)/)
  assert.match(pool, /onPanelProjectMutated\?\.\(projectId\)/)
})

test('stream admission is synchronous and lease release clears local CAS first', () => {
  const admission = chat.slice(chat.indexOf('const tryStartStream'), chat.indexOf('const tryStartStreamRef'))
  assert.match(admission, /streamInFlightRef\.current \|\| uploadInFlightRef\.current/)
  assert.match(admission, /const admission = acquireStreamLease\(projectId\)/)
  assert.ok(admission.indexOf('acquireStreamLease(projectId)') < admission.indexOf('startStream(options)'))
  const clear = admission.indexOf('streamInFlightRef.current = false')
  const release = admission.indexOf('releaseStreamLease(admission.token)')
  assert.ok(clear > -1 && release > clear, 'must clear local CAS before releasing the global lease')
  assert.match(admission, /void promise\.catch\(/)
  assert.doesNotMatch(admission, /\bdeleting\b/, 'rendered deletion state is stale; core admission is the sole gate')
  assert.doesNotMatch(pool, /deleting=\{/, 'pool must not pass an asynchronous deletion snapshot')
})

test('ChatPanel keeps its imperative handle stable so rerenders cannot cancel cap waiters', () => {
  assert.match(chat, /const imperativeMethodsRef = useRef\(null\)/)
  assert.match(chat, /const stableImperativeHandleRef = useRef\(null\)/)
  assert.match(chat, /useImperativeHandle\(ref, \(\) => stableImperativeHandleRef\.current, \[\]\)/)
  assert.match(chat, /flushPendingTriggers: \(\.\.\.args\) => imperativeMethodsRef\.current\?\.flushPendingTriggers\?\.\(\.\.\.args\)/)
})

test('upload cleanup clears the local record, ends its exact token, then flushes local triggers', () => {
  const finish = chat.slice(chat.indexOf('const finishUploadRecord'), chat.indexOf('const mergeMaterialIds'))
  assert.ok(finish.indexOf('uploadInFlightRef.current = null') < finish.indexOf('endUpload(record.token)'))
  const send = chat.slice(chat.indexOf('const sendMessage = async'), chat.indexOf('const handleSelectFiles'))
  const cleanup = send.indexOf('finishUploadRecord(uploadRecord)')
  const flush = send.indexOf('flushPendingTriggersRef.current?.()')
  assert.ok(cleanup > -1 && flush > cleanup)
  assert.match(send, /shouldContinueAfterUpload\(\{/)
})

test('App keeps active and pid-aware pool callback contracts separate', () => {
  assert.match(app, /const handleActiveMaterialsMerged = \(incomingMaterials\)/)
  assert.match(app, /const handlePanelMaterialsMerged = \(projectId, incomingMaterials\)/)
  assert.match(app, /const handleActiveProjectMutated = \(\)/)
  assert.match(app, /const handlePanelProjectMutated = \(projectId\)/)
  assert.match(app, /projectId === currentProjectIdRef\.current/)
  assert.match(app, /onPanelMaterialsMerged=\{handlePanelMaterialsMerged\}/)
  assert.match(app, /onMaterialsMerged=\{handleActiveMaterialsMerged\}/)
})

test('delete, workspace upload, and logout delegate to the shared pool protocols', () => {
  assert.match(app, /tryBeginDelete\?\.\(projectId\)/)
  assert.match(app, /abortProjectWork\?\.\(projectId\)/)
  assert.match(app, /finishDelete\?\.\(admission\.token, \{ forgotten \}\)/)
  assert.match(workspace, /const uploadToken = beginUpload\(requestProject\)/)
  assert.match(workspace, /finally \{\s*endUpload\(uploadToken\)/)
  assert.match(app, /runLogout\(\{/)
  assert.match(app, /abortAll: \(\) => chatPanelRef\.current\?\.abortAll\?\.\(\)/)
  assert.match(app, /setPendingAutoStartProjectIds\(new Set\(\)\)/)
})
