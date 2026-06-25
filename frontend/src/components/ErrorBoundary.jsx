import React from 'react'

// 共享错误边界。**在 main.jsx 包住整个 <App/>**，使任何渲染错误（含 Login/ForcePasswordChange
// 等 App 早返回分支——它们不在 App 内部那层 ErrorBoundary 里）只显示友好兜底，而不是整树卸载
// 成白屏。2026-06-23：422 校验错误的数组 detail 被塞进 Login 的 {err} 触发渲染崩溃 → 白屏，
// 根因已在 normalizeAuthError 修掉，这层是纵深防御，防未来任何 auth 屏渲染错误再白屏。
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center h-screen bg-bg">
          <div className="text-error">应用出错，请刷新页面</div>
        </div>
      )
    }
    return this.props.children
  }
}
