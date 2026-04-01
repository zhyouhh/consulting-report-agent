export function prepareProjectCreatePayload(formData = {}) {
  const theme = (formData.theme || "").trim();
  const meaningfulToken = theme
    .replace(/\s+/g, "-")
    .replace(/[^\w\u4e00-\u9fa5-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");

  if (!meaningfulToken) {
    throw new Error("请输入有效的报告主题");
  }

  return {
    name: theme,
    workspace_dir: (formData.workspace_dir || "").trim(),
    project_type: formData.project_type || "strategy-consulting",
    theme,
    target_audience: formData.target_audience || "",
    deadline: (formData.deadline || "").trim(),
    expected_length: (formData.expected_length || "").trim(),
    notes: "",
    initial_material_paths: formData.initial_material_paths || [],
  };
}
