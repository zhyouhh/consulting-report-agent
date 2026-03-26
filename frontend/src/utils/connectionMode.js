export function describeConnectionMode(settings = {}) {
  if (!settings.mode || settings.mode === "managed") {
    return {
      title: "默认通道",
      subtitle: `推荐，开箱即用 · ${settings.managed_model || "gemini-3-flash"}`,
    };
  }

  return {
    title: "自定义 API",
    subtitle: settings.custom_model || "高级配置，自行承担可用性",
  };
}
