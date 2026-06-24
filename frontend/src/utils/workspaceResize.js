// 主布局「中间聊天区 ↔ 右侧工作区」分隔条的宽度计算（左右拖动）。
// 与文件预览面板上下分栏（filePanelLayout.js）同模式：纯函数 + 常量，便于无 jsdom 单测，
// 组件只负责接事件。工作区贴右缘，鼠标越往左 → 工作区越宽。

export const DEFAULT_WORKSPACE_WIDTH = 448; // 28rem，保持改造前默认宽度
export const MIN_WORKSPACE_WIDTH = 320;
// 拖动时给「聊天区一侧」保留的最小宽度。注意：clamp 用的容器是「聊天区 + 6px 分隔条 + 工作区」
// 可调区域，故这是「聊天列 + 分隔条」的下限，聊天列净宽 ≈ MIN_CHAT_WIDTH − 分隔条(6px)，差值可忽略。
export const MIN_CHAT_WIDTH = 360;
export const MAX_WORKSPACE_WIDTH = 1100; // 绝对上限兜底（极宽屏下不让工作区独占）

// 把宽度夹到 [下限, 上限]。给了容器宽度时，上限再受「容器宽 - 聊天区最小宽」约束，
// 保证聊天区永不被挤到 MIN_CHAT_WIDTH 以下；容器极窄时下限同步降到该上限（优先保聊天区可见）。
export function clampWorkspaceWidth(width, containerWidth) {
  if (!Number.isFinite(width)) return DEFAULT_WORKSPACE_WIDTH;
  let maxAllowed = MAX_WORKSPACE_WIDTH;
  if (Number.isFinite(containerWidth) && containerWidth > 0) {
    maxAllowed = Math.min(maxAllowed, containerWidth - MIN_CHAT_WIDTH);
  }
  // 容器比聊天区最小宽还窄时 maxAllowed 会变负——夹到 0，绝不返回负宽度（会产生非法 CSS）。
  maxAllowed = Math.max(0, maxAllowed);
  const lowerBound = Math.min(MIN_WORKSPACE_WIDTH, maxAllowed);
  return Math.min(maxAllowed, Math.max(lowerBound, width));
}

// 由鼠标 X 与容器矩形换算工作区宽度（拖动分隔条时调用）。
export function computeWorkspaceWidth(clientX, containerRect) {
  if (!containerRect || !containerRect.width) return DEFAULT_WORKSPACE_WIDTH;
  const width = containerRect.right - clientX; // 工作区贴右缘
  return clampWorkspaceWidth(width, containerRect.width);
}

// 解析 localStorage 存的宽度（初始化时无容器，仅夹绝对上下限），缺失/坏值回落默认。
// 注意：缺失值必须显式判 null/undefined/""——Number(null) 与 Number("") 都是 0（finite），
// 会被错误夹到 MIN 而非回落默认。
export function parseStoredWorkspaceWidth(raw) {
  if (raw === null || raw === undefined || raw === "") return DEFAULT_WORKSPACE_WIDTH;
  const n = Number(raw);
  if (!Number.isFinite(n)) return DEFAULT_WORKSPACE_WIDTH;
  return clampWorkspaceWidth(n);
}
