// N6 D2: pure helpers for SSE side events on the chat bubble.
//
// The backend emits `attachment_transcribed` events `{ message_id, attachment_id, status }`
// while a normal user turn is being persisted. `message_id` is the client_message_id that the
// frontend assigned to the user bubble; `attachment_id` is the pending-attachment id that
// `buildTransientAttachmentsPayload` carried through. This lets us mark the exact attachment on
// the exact bubble as transcribed / failed, without mutating message content.

/**
 * Return a NEW messages array where the message whose id === message_id has its attachment
 * (matched by attachment_id) tagged with the transcription status. No match → messages unchanged
 * (same reference). Pure: never mutates inputs.
 *
 * @param {Array<object>} messages
 * @param {{message_id?: string, attachment_id?: string, status?: string}} event
 * @returns {Array<object>}
 */
export function applyAttachmentTranscribed(messages = [], event = {}) {
  const { message_id: messageId, attachment_id: attachmentId, status } = event || {};
  if (!Array.isArray(messages) || !messageId || !attachmentId) {
    return messages;
  }

  let matched = false;
  const next = messages.map((message) => {
    if (!message || message.id !== messageId) {
      return message;
    }
    const attachments = Array.isArray(message.transientAttachments)
      ? message.transientAttachments
      : [];
    let touched = false;
    const nextAttachments = attachments.map((attachment) => {
      if (!attachment || attachment.id !== attachmentId) {
        return attachment;
      }
      touched = true;
      return {
        ...attachment,
        transcriptionStatus: status,
        transcribed: status === "parsed",
      };
    });
    if (!touched) {
      return message;
    }
    matched = true;
    return { ...message, transientAttachments: nextAttachments };
  });

  return matched ? next : messages;
}
