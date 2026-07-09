// 显式 .js 扩展：node:test 的 ESM 解析不吃无扩展相对导入（Vite 两者都认）。
import { FILE_DISPLAY_NAMES } from "./fileTree.js";

// 文件内链（2026-07-09 试用反馈③）：工具 pill 的路径实参、助手正文里反引号提到的
// 文件名，可点击直达右侧「文件」tab。白名单 = 文件树已知文件全集（FILE_DISPLAY_NAMES
// 单一真值源），精确匹配（完整路径或唯一 basename），不做模糊匹配——杜绝误跳。

const EXACT_LINK_TARGETS = new Map();
for (const canonicalPath of Object.keys(FILE_DISPLAY_NAMES)) {
  EXACT_LINK_TARGETS.set(canonicalPath, canonicalPath);
  const basename = canonicalPath.split("/").pop();
  // basename 撞名则裸文件名不可解析（当前全集唯一；防未来加重名文件后静默误跳）。
  if (EXACT_LINK_TARGETS.has(basename)) {
    EXACT_LINK_TARGETS.set(basename, null);
  } else {
    EXACT_LINK_TARGETS.set(basename, canonicalPath);
  }
}

// 文本（trim 后）精确命中已知工作区文件 → 返回规范路径，否则 null。
export function resolveWorkspaceFileLink(text) {
  if (typeof text !== "string") return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  return EXACT_LINK_TARGETS.get(trimmed) || null;
}

// arg 即文件路径的工具（_sse_tool_arg 取首参 = file_path）。
const PATH_ARG_TOOLS = new Set(["write_file", "edit_file", "read_file"]);

// 从工具事件推导可跳转文件：append_report_draft 目标恒为 canonical 草稿（其 arg 为空）；
// 路径实参工具按白名单解析（40 字符截断的 arg 自然匹配不上 → 不给链接）。
export function pathFromToolEvent(event) {
  if (!event || typeof event !== "object") return null;
  if (event.tool === "append_report_draft") return "content/report_draft_v1.md";
  if (!PATH_ARG_TOOLS.has(event.tool)) return null;
  return resolveWorkspaceFileLink(event.arg);
}
