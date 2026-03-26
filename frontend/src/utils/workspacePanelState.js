export function getNextQualityResult({
  currentResult,
  previousProject,
  nextProject,
}) {
  if (previousProject === nextProject && nextProject) {
    return currentResult;
  }

  return null;
}
