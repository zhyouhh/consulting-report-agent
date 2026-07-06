import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import AdminPage from './components/AdminPage.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css'
import './api'

// 极简路径路由（2026-07-06）：/admin 渲染管理控制台整页，其余渲染主应用。
// 后端 _SPAStaticFiles 对 /admin 回退 index.html（白名单，见 backend/main.py），
// 前端按 pathname 分流——不引路由库（当前只有两个页面，奥卡姆剃刀）。
const isAdminRoute = /^\/admin\/?$/.test(window.location.pathname)

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      {isAdminRoute ? <AdminPage /> : <App />}
    </ErrorBoundary>
  </React.StrictMode>,
)
