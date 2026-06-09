export function initialEditState() {
  return { mode: "preview", draft: "", baseMtimeNs: null, saving: false, dirty: false };
}

export function enterEdit(state, { content, mtimeNs }) {
  return { mode: "edit", draft: content, baseMtimeNs: mtimeNs, saving: false, dirty: false };
}

export function editDraft(state, nextDraft) {
  if (state.mode !== "edit") return state;
  return { ...state, draft: nextDraft, dirty: true };
}

export function cancelEdit() {
  return initialEditState();
}

export function startSaving(state) {
  if (state.mode !== "edit") return state;
  return { ...state, saving: true };
}

export function saveSucceeded() {
  return initialEditState();
}

export function saveFailed(state) {
  return { ...state, saving: false };
}

export function reloadAfterConflict(state, { content, mtimeNs }) {
  return { mode: "edit", draft: content, baseMtimeNs: mtimeNs, saving: false, dirty: false };
}

// guardLeave: decide what happens when the user tries to leave the current edit context.
//   'allow'   — no edit, or edit with no unsaved changes
//   'confirm' — dirty: caller should confirm discard before leaving
//   'block'   — a save is in flight: caller must refuse to leave
export function guardLeave(state) {
  if (state.saving) return "block";
  if (state.mode === "edit" && state.dirty) return "confirm";
  return "allow";
}
