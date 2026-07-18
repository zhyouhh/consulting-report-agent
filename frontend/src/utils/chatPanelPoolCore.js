export const NO_PROJECT_SENTINEL = '__no_project__'
export const MAX_CONCURRENT_CHAT_STREAMS = 3

const validProjectId = (pid) => (
  typeof pid === 'string' && pid.length > 0 && pid !== NO_PROJECT_SENTINEL
)

const nextToken = (core, prefix, pid) => {
  core.tokenSeq += 1
  return `${prefix}:${pid}:${core.tokenSeq}`
}

export function createChatPanelPoolCore({
  maxConcurrentStreams = MAX_CONCURRENT_CHAT_STREAMS,
  onChange = null,
} = {}) {
  const core = {
    maxConcurrentStreams,
    onChange,
    tokenSeq: 0,
    members: new Set(),
    leasesByPid: new Map(),
    leasePidByToken: new Map(),
    waiterQueue: [],
    waiterSet: new Set(),
    flushers: new Map(),
    uploadsByPid: new Map(),
    uploadPidByToken: new Map(),
    deleteByPid: new Map(),
    deletePidByToken: new Map(),
  }

  const changed = () => core.onChange?.()

  const visitProject = (pid) => {
    if (!validProjectId(pid) || core.members.has(pid)) return false
    core.members.add(pid)
    changed()
    return true
  }

  const computeMounted = (activeProjectId) => {
    if (!validProjectId(activeProjectId)) return [...core.members, NO_PROJECT_SENTINEL]
    visitProject(activeProjectId)
    return [...core.members]
  }

  const cancelWaiter = (pid) => {
    if (!core.waiterSet.delete(pid)) return false
    core.waiterQueue = core.waiterQueue.filter(item => item !== pid)
    changed()
    return true
  }

  const enqueuePermitWaiter = (pid) => {
    if (!validProjectId(pid) || core.waiterSet.has(pid)) return false
    core.waiterSet.add(pid)
    core.waiterQueue.push(pid)
    changed()
    return true
  }

  const registerPermitFlusher = (pid, flusher) => {
    if (!validProjectId(pid) || typeof flusher !== 'function') return () => {}
    core.flushers.set(pid, flusher)
    return () => {
      if (core.flushers.get(pid) === flusher) core.flushers.delete(pid)
      cancelWaiter(pid)
    }
  }

  const acquireStreamLease = (pid) => {
    if (!validProjectId(pid)) return { status: 'missing' }
    if (core.deleteByPid.has(pid)) return { status: 'deleting' }
    if (!core.members.has(pid)) return { status: 'missing' }
    if (core.leasesByPid.has(pid)) return { status: 'same_pid' }
    if (core.leasesByPid.size >= core.maxConcurrentStreams) return { status: 'cap_full' }
    const token = nextToken(core, 'stream', pid)
    core.leasesByPid.set(pid, token)
    core.leasePidByToken.set(token, pid)
    changed()
    return { status: 'started', token }
  }

  const drainPermitWaiters = () => {
    const visited = new Set()
    while (core.waiterQueue.length > 0) {
      const pid = core.waiterQueue.shift()
      core.waiterSet.delete(pid)
      if (visited.has(pid)) continue
      visited.add(pid)

      if (core.deleteByPid.has(pid)) continue
      const flusher = core.flushers.get(pid)
      if (!core.members.has(pid) || typeof flusher !== 'function') continue

      const status = flusher()?.status || 'empty'
      if (status === 'started') {
        changed()
        return { status: 'started', pid }
      }
      if (status === 'cap_full') {
        enqueuePermitWaiter(pid)
        return { status: 'cap_full', pid }
      }
      // local_busy is deliberately not requeued: the panel's local settle/upload
      // wake-up owns the next attempt. deleting/empty/stale/missing are discarded.
    }
    changed()
    return { status: 'empty' }
  }

  const releaseStreamLease = (token) => {
    const pid = core.leasePidByToken.get(token)
    if (!pid || core.leasesByPid.get(pid) !== token) return false
    core.leasePidByToken.delete(token)
    core.leasesByPid.delete(pid)
    changed()
    drainPermitWaiters()
    return true
  }

  const beginUpload = (pid) => {
    if (!validProjectId(pid) || core.deleteByPid.has(pid) || !core.members.has(pid)) return null
    const token = nextToken(core, 'upload', pid)
    const tokens = core.uploadsByPid.get(pid) || new Set()
    tokens.add(token)
    core.uploadsByPid.set(pid, tokens)
    core.uploadPidByToken.set(token, pid)
    changed()
    return token
  }

  const endUpload = (token) => {
    const pid = core.uploadPidByToken.get(token)
    if (!pid) return null
    core.uploadPidByToken.delete(token)
    const tokens = core.uploadsByPid.get(pid)
    tokens?.delete(token)
    if (!tokens?.size) core.uploadsByPid.delete(pid)
    changed()
    return pid
  }

  const tryBeginDelete = (pid) => {
    // Sidebar may delete a valid project that this tab has never opened, so membership is not a
    // project-existence registry. Only stream/upload handles require membership to block ghosts.
    if (!validProjectId(pid)) return { status: 'missing' }
    if (core.deleteByPid.has(pid)) return { status: 'deleting' }
    if (core.uploadsByPid.get(pid)?.size) return { status: 'uploading' }
    const token = nextToken(core, 'delete', pid)
    core.deleteByPid.set(pid, token)
    core.deletePidByToken.set(token, pid)
    cancelWaiter(pid)
    changed()
    return { status: 'started', token }
  }

  const clearProjectRuntime = (pid) => {
    cancelWaiter(pid)
    const lease = core.leasesByPid.get(pid)
    if (lease) {
      core.leasesByPid.delete(pid)
      core.leasePidByToken.delete(lease)
    }
    const uploads = core.uploadsByPid.get(pid)
    if (uploads) {
      uploads.forEach(token => core.uploadPidByToken.delete(token))
      core.uploadsByPid.delete(pid)
    }
    changed()
  }

  const forgetProject = (pid) => {
    if (!validProjectId(pid)) return false
    clearProjectRuntime(pid)
    const deleteToken = core.deleteByPid.get(pid)
    if (deleteToken) core.deletePidByToken.delete(deleteToken)
    core.deleteByPid.delete(pid)
    core.flushers.delete(pid)
    const removed = core.members.delete(pid)
    changed()
    return removed
  }

  const finishDelete = (token, { forgotten = false } = {}) => {
    const pid = core.deletePidByToken.get(token)
    if (!pid || core.deleteByPid.get(pid) !== token) return 'stale'
    core.deletePidByToken.delete(token)
    core.deleteByPid.delete(pid)
    if (forgotten) {
      forgetProject(pid)
      return 'forgotten'
    }
    changed()
    return 'resume_required'
  }

  return {
    visitProject,
    computeMounted,
    acquireStreamLease,
    releaseStreamLease,
    enqueuePermitWaiter,
    cancelWaiter,
    registerPermitFlusher,
    drainPermitWaiters,
    beginUpload,
    endUpload,
    tryBeginDelete,
    deleteProjectIdForToken: (token) => core.deletePidByToken.get(token) || null,
    finishDelete,
    forgetProject,
    clearProjectRuntime,
    busyProjectIds: () => new Set(core.leasesByPid.keys()),
    isDeleting: (pid) => core.deleteByPid.has(pid),
    snapshot: () => ({
      members: [...core.members],
      leases: [...core.leasesByPid.keys()],
      waiters: [...core.waiterQueue],
      uploads: [...core.uploadsByPid].map(([pid, tokens]) => [pid, tokens.size]),
      deleting: [...core.deleteByPid.keys()],
    }),
  }
}

export function shouldContinueAfterUpload({ mounted, accepting }) {
  return mounted === true && accepting === true
}

export function runTwoPassShutdown(handles = []) {
  const live = handles.filter(Boolean)
  live.forEach(handle => handle.stopAcceptingWork?.())
  live.forEach(handle => {
    handle.abortActiveStream?.()
    handle.cancelActiveUpload?.()
    handle.cancelPendingWork?.()
  })
}

export async function runLogout({ abortAll, requestLogout, clearSession }) {
  abortAll?.()
  try {
    await requestLogout()
  } finally {
    clearSession()
  }
}
