const PREFERRED_PREVIEW_PATH = "plan/project-overview.md";
const LEGACY_PREVIEW_PATH = "plan/project-info.md";

export function getDefaultPreviewFile(paths = []) {
  if (paths.includes(PREFERRED_PREVIEW_PATH)) {
    return PREFERRED_PREVIEW_PATH;
  }

  if (paths.includes(LEGACY_PREVIEW_PATH)) {
    return LEGACY_PREVIEW_PATH;
  }

  return paths[0] || "";
}

export function orderPreviewFiles(paths = []) {
  const uniquePaths = [...new Set(paths)];
  const orderedPaths = uniquePaths
    .filter((path) => path !== PREFERRED_PREVIEW_PATH && path !== LEGACY_PREVIEW_PATH)
    .sort((left, right) => left.localeCompare(right));

  if (uniquePaths.includes(PREFERRED_PREVIEW_PATH)) {
    orderedPaths.unshift(PREFERRED_PREVIEW_PATH);
  }

  if (uniquePaths.includes(LEGACY_PREVIEW_PATH)) {
    orderedPaths.push(LEGACY_PREVIEW_PATH);
  }

  return orderedPaths;
}
