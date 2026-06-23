import axios from 'axios'

axios.defaults.withCredentials = true

let onUnauthed = null
export function setUnauthedHandler(fn) { onUnauthed = fn }

axios.interceptors.response.use(
  (r) => r,
  (error) => {
    // handler 抛错也不能替掉原始 axios error——调用方永远应收到原错（Codex review）。
    // skipUnauthedHandler：背景轮询（如额度刷新 refreshAuthQuota）显式跳过全局登出副作用——
    // 后台 poll 不该是「把你踢到登录页」的那个请求（旧用户登出后在途 /me 返 401 会误踢新用户）；
    // 真正的 axios 用户请求（加载项目/设置等）若 401 仍正常触发登出（聊天流式走 fetch、另行处理）。
    if (error?.response?.status === 401 && onUnauthed && !error.config?.skipUnauthedHandler) {
      try { onUnauthed() } catch (_) { /* swallow handler error */ }
    }
    return Promise.reject(error)
  }
)
