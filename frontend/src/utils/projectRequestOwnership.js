export function shouldApplyProjectResponse({
  requestProject,
  activeProject,
}) {
  return Boolean(requestProject) && requestProject === activeProject;
}
