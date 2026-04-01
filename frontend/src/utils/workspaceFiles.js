const PREFERRED_PREVIEW_PATH = "plan/project-overview.md";
const RETIRED_PREVIEW_PATH = "plan/project-info.md";

export function getDefaultPreviewFile(paths = []) {
  const visiblePaths = [...new Set(paths)].filter((path) => path !== RETIRED_PREVIEW_PATH);

  if (visiblePaths.includes(PREFERRED_PREVIEW_PATH)) {
    return PREFERRED_PREVIEW_PATH;
  }

  return visiblePaths[0] || "";
}

export function orderPreviewFiles(paths = []) {
  const uniquePaths = [...new Set(paths)];
  const orderedPaths = uniquePaths
    .filter((path) => path !== PREFERRED_PREVIEW_PATH && path !== RETIRED_PREVIEW_PATH)
    .sort((left, right) => left.localeCompare(right));

  if (uniquePaths.includes(PREFERRED_PREVIEW_PATH)) {
    orderedPaths.unshift(PREFERRED_PREVIEW_PATH);
  }

  return orderedPaths;
}
