import axios from 'axios'

axios.defaults.withCredentials = true

let onUnauthed = null
export function setUnauthedHandler(fn) { onUnauthed = fn }

axios.interceptors.response.use(
  (r) => r,
  (error) => {
    // handler 抛错也不能替掉原始 axios error——调用方永远应收到原错（Codex review）。
    if (error?.response?.status === 401 && onUnauthed) {
      try { onUnauthed() } catch (_) { /* swallow handler error */ }
    }
    return Promise.reject(error)
  }
)
