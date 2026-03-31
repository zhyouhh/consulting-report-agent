export function shouldSubmitComposerKeydown({ key, shiftKey, isComposing }) {
  return key === "Enter" && !shiftKey && !isComposing;
}
