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

// N6 D2: build the transient-attachment payload sent to the backend. Each item PRESERVES the
// pending attachment `id` so the backend's `attachment_transcribed` SSE event (which echoes
// `attachment_id`) can be correlated back to the exact bubble attachment on the frontend.
// `data_url` is resolved by the caller (async file read) and passed in per item.
export function buildTransientAttachmentsPayload(resolvedAttachments = []) {
  return resolvedAttachments.map((attachment) => ({
    id: attachment.id,
    name: attachment.name,
    mime_type: attachment.mime_type,
    data_url: attachment.data_url,
  }));
}

// N6 D2: map a material's backend conversion_status to a small Chinese chip label + tooltip.
// v1 status set is exactly {not_parsed, parsed, failed} — there is no "parsing" state.
// Returns null for not_parsed (no chip), so the materials list stays quiet until something happens.
export function conversionStatusChip(material = {}) {
  const status = material?.conversion_status;
  if (status === "parsed") {
    return { label: "已解析", tone: "parsed", title: null };
  }
  if (status === "failed") {
    return { label: "失败", tone: "failed", title: material?.conversion_reason || "转换失败" };
  }
  return null;
}

export function buildChatRequest({
  projectId,
  messageText = "",
  attachedMaterialIds = [],
  transientAttachments = [],
  systemTrigger = null,
  triggerMetadata = null,
  clientMessageId = null,
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
  // client_message_id correlates the user bubble with attachment_transcribed SSE events. It is
  // OMITTED on system_trigger turns (those render no user bubble, so there is nothing to mark).
  if (!systemTrigger && clientMessageId) {
    payload.client_message_id = clientMessageId;
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
