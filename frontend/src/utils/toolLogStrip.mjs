const TOOL_LOG_COMMENT_RE = /<!--\s*tool-log(?:[\s\S]*?-->|[\s\S]*$)/gi

export function stripToolLogComments(content) {
  if (!content) return content
  return content.replace(TOOL_LOG_COMMENT_RE, '').trimEnd()
}
