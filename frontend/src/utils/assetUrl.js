// 预览 img src 重写（图表 spec §4.5）：草稿里的相对引用 `assets/<id>.png` 重写到
// 二进制资产路由 /api/projects/<pid>/assets/<id>.png（require_project 租户隔离）。
// 路由根比 markdown 相对根深一层 assets/，所以拼 URL 前剥掉前导 `assets/` 段。
// chart_id 每次铸新、PNG 内容不可变 → 无需 cache-bust，浏览器可长缓存。
// 绝对 URL / data: / http(s) / 非 assets 相对路径一律原样返回（不重写）。

export function resolveAssetSrc(src, projectId) {
  if (typeof src !== 'string' || !src) return src
  if (!projectId) return src
  if (/^(?:[a-z][a-z0-9+.-]*:|\/\/|\/)/i.test(src)) return src // 绝对 / data: / 协议相对 / 根相对
  const match = /^(?:\.\/)?assets\/([^/?#]+\.png)(?:[?#].*)?$/i.exec(src)
  if (!match) return src
  return `/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(
    decodeSafe(match[1]),
  )}`
}

// 草稿里可能已是 URL 编码形态；先解再编，避免双重编码。解码失败按原文编码。
function decodeSafe(segment) {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}
