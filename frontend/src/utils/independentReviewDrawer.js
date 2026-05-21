export function parseDrawerEvent(data) {
  if (!data || data === "[DONE]") return null;
  try {
    const parsed = JSON.parse(data);
    if (!parsed || typeof parsed !== "object" || typeof parsed.type !== "string") {
      return null;
    }
    if (parsed.type === "error") {
      const message = [parsed.detail, parsed.data, parsed.message]
        .find(value => typeof value === "string" && value.trim().length > 0) || "审查失败";
      return { ...parsed, message };
    }
    return parsed;
  } catch {
    return null;
  }
}
