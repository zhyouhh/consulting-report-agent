import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

import ChatPanel from './ChatPanel'
import {
  NO_PROJECT_SENTINEL,
  createChatPanelPoolCore,
  runTwoPassShutdown,
} from '../utils/chatPanelPoolCore'

const ChatPanelPool = forwardRef(function ChatPanelPool({
  activeProjectId,
  panelProps = {},
  activeOnlyProps = {},
  pendingAutoStartProjectIds,
  onAutoStartConsumed,
  onPanelMaterialsMerged,
  onPanelProjectMutated,
  onBusyIndicatorChange,
}, ref) {
  const [, setVersion] = useState(0)
  const poolMountedRef = useRef(true)
  const coreRef = useRef(null)
  if (!coreRef.current) {
    coreRef.current = createChatPanelPoolCore({
      onChange: () => {
        if (poolMountedRef.current) setVersion(value => value + 1)
      },
    })
  }
  const core = coreRef.current
  const panelRefs = useRef(new Map())
  const panelRefCallbacks = useRef(new Map())
  const flusherCleanups = useRef(new Map())

  const snapshot = core.snapshot()
  const activeKey = activeProjectId || NO_PROJECT_SENTINEL
  const mountedIds = activeProjectId
    ? [...new Set([...snapshot.members, activeProjectId])]
    : [...snapshot.members, NO_PROJECT_SENTINEL]

  const getPanelRefCallback = (pid, projectId) => {
    let callback = panelRefCallbacks.current.get(pid)
    if (callback) return callback
    callback = (handle) => {
      flusherCleanups.current.get(pid)?.()
      flusherCleanups.current.delete(pid)
      if (handle) {
        panelRefs.current.set(pid, handle)
        if (projectId) {
          flusherCleanups.current.set(
            pid,
            core.registerPermitFlusher(
              projectId,
              () => handle.flushPendingTriggers?.() || { status: 'empty' },
            ),
          )
        }
      } else {
        panelRefs.current.delete(pid)
        if (projectId) core.cancelWaiter(projectId)
      }
    }
    panelRefCallbacks.current.set(pid, callback)
    return callback
  }

  // Register the active project before child passive effects can finish conversation loading and
  // trigger auto-start. Admission rejects non-members so a forgotten panel cannot create ghost work.
  useLayoutEffect(() => {
    if (activeProjectId) core.visitProject(activeProjectId)
  }, [activeProjectId, core])

  useEffect(() => {
    poolMountedRef.current = true
    return () => {
      poolMountedRef.current = false
    }
  }, [])

  useEffect(() => {
    onBusyIndicatorChange?.(core.busyProjectIds())
  }, [snapshot.leases.join('|'), core, onBusyIndicatorChange])

  useImperativeHandle(ref, () => ({
    triggerSystemTurn: (type, metadata) => panelRefs.current.get(activeKey)?.triggerSystemTurn?.(type, metadata),
    dropPendingReviewTriggers: (type) => panelRefs.current.get(activeKey)?.dropPendingReviewTriggers?.(type),
    sendUserMessage: (text) => panelRefs.current.get(activeKey)?.sendUserMessage?.(text) ?? false,
    abortProjectWork: (pid) => panelRefs.current.get(pid)?.abortActiveStream?.(),
    flushPendingTriggers: (pid) => panelRefs.current.get(pid)?.flushPendingTriggers?.() || { status: 'missing' },
    tryBeginDelete: (pid) => core.tryBeginDelete(pid),
    finishDelete: (token, options) => {
      const pid = core.deleteProjectIdForToken(token)
      const result = core.finishDelete(token, options)
      if (result === 'resume_required' && pid) panelRefs.current.get(pid)?.flushPendingTriggers?.()
      return result
    },
    beginUpload: (pid) => core.beginUpload(pid),
    endUpload: (token) => core.endUpload(token),
    forgetProject: (pid) => {
      panelRefs.current.get(pid)?.cancelPendingWork?.()
      flusherCleanups.current.get(pid)?.()
      flusherCleanups.current.delete(pid)
      panelRefs.current.delete(pid)
      panelRefCallbacks.current.delete(pid)
      return core.forgetProject(pid)
    },
    abortAll: () => {
      runTwoPassShutdown([...panelRefs.current.values()])
      for (const pid of core.snapshot().members) core.clearProjectRuntime(pid)
    },
  }), [activeKey, core])

  return mountedIds.map((pid) => {
    const isSentinel = pid === NO_PROJECT_SENTINEL
    const projectId = isSentinel ? null : pid
    const visible = pid === activeKey
    const isAutoStartPending = projectId ? pendingAutoStartProjectIds?.has?.(projectId) : false
    return (
      <div key={pid} style={{ display: visible ? 'contents' : 'none' }}>
        <ChatPanel
          {...panelProps}
          {...(visible ? activeOnlyProps : {
            project: null,
            workspace: null,
            materials: [],
            injectedPrompt: null,
            onInjectedPromptConsumed: null,
          })}
          ref={getPanelRefCallback(pid, projectId)}
          projectId={projectId}
          visible={visible}
          acquireStreamLease={core.acquireStreamLease}
          releaseStreamLease={core.releaseStreamLease}
          enqueuePermitWaiter={core.enqueuePermitWaiter}
          cancelPermitWaiter={core.cancelWaiter}
          beginUpload={core.beginUpload}
          endUpload={core.endUpload}
          autoStartProjectId={isAutoStartPending ? projectId : null}
          onAutoStartConsumed={() => projectId && onAutoStartConsumed?.(projectId)}
          onMaterialsMerged={(materials) => projectId && onPanelMaterialsMerged?.(projectId, materials)}
          onProjectMutated={() => projectId && onPanelProjectMutated?.(projectId)}
        />
      </div>
    )
  })
})

export default ChatPanelPool
