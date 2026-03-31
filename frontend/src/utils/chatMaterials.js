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
  messageText,
  attachedMaterialIds = [],
  transientAttachments = [],
}) {
  const payload = {
    project_id: projectId,
    message_text: messageText.trim(),
    attached_material_ids: attachedMaterialIds,
  };
  if (transientAttachments.length > 0) {
    payload.transient_attachments = transientAttachments;
  }
  return payload;
}
