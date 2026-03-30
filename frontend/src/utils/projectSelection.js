export function getCurrentProject(projects, currentProjectId) {
  if (!currentProjectId) {
    return null;
  }

  return projects.find((project) => project.id === currentProjectId) || null;
}

export function reconcileCurrentProjectId(projects, currentProjectId) {
  return getCurrentProject(projects, currentProjectId)?.id || null;
}

export function isSameProjectSelection(currentProjectId, nextProjectId) {
  if (!currentProjectId || !nextProjectId) {
    return false;
  }

  return currentProjectId === nextProjectId;
}
