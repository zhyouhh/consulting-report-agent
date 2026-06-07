export function mergeMaterials(existingMaterials = [], incomingMaterials = []) {
  const merged = [...existingMaterials];
  const seenIds = new Set(existingMaterials.map((material) => material.id));

  for (const material of incomingMaterials) {
    if (!material?.id || seenIds.has(material.id)) {
      continue;
    }
    merged.push(material);
    seenIds.add(material.id);
  }

  return merged;
}

export function removeMaterialById(materials = [], materialId) {
  return materials.filter((material) => material.id !== materialId);
}

export function toggleMaterialSelection(selectedMaterialIds = [], materialId) {
  if (!materialId) {
    return selectedMaterialIds;
  }

  if (selectedMaterialIds.includes(materialId)) {
    return selectedMaterialIds.filter((currentId) => currentId !== materialId);
  }

  return [...selectedMaterialIds, materialId];
}

export function buildChatRequest({
  projectId,
  messageText = "",
  attachedMaterialIds = [],
  transientAttachments = [],
  systemTrigger = null,
  triggerMetadata = null,
}) {
  const trimmed = typeof messageText === "string" ? messageText.trim() : "";
  const payload = {
    project_id: projectId,
    message_text: systemTrigger ? messageText : trimmed,
  };
  if (attachedMaterialIds.length > 0) {
    payload.attached_material_ids = attachedMaterialIds;
  }
  if (transientAttachments.length > 0) {
    payload.transient_attachments = transientAttachments;
  }
  if (systemTrigger) {
    payload.system_trigger = systemTrigger;
  }
  // C5: run-bound trigger metadata travels with a system trigger so the backend can bind a
  // review report to the exact run that produced it. run_id / report_mtime_ns are OPAQUE
  // STRINGS — write them through verbatim, never Number()/parseInt (a nanosecond mtime
  // exceeds JS Number.MAX_SAFE_INTEGER and would be silently rounded).
  if (triggerMetadata) {
    if (triggerMetadata.run_id != null) {
      payload.run_id = triggerMetadata.run_id;
    }
    if (triggerMetadata.report_mtime_ns != null) {
      payload.report_mtime_ns = triggerMetadata.report_mtime_ns;
    }
  }
  return payload;
}
