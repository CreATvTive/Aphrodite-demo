export const DIALOGUE_PROTOCOL_VERSION = "aphrodite.chatbox.dialogue-ws/1";
export const MAX_DIALOGUE_TEXT_CHARS = 4000;

const SERVER_TYPES = new Set([
  "hello", "history", "turn.accepted", "turn.stream", "turn.completed",
  "turn.degraded", "turn.cancelled", "turn.error", "proactive.stream",
]);

export class DialogueProtocolViolation extends Error {
  constructor(message) {
    super(message);
    this.name = "DialogueProtocolViolation";
  }
}

function objectValue(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new DialogueProtocolViolation(`${label} must be an object`);
  }
  return value;
}

function requiredString(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new DialogueProtocolViolation(`${label} must be a non-empty string`);
  }
  return value;
}

function turnId(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(value)) {
    throw new DialogueProtocolViolation("client_turn_id is invalid");
  }
  return value;
}

function nonNegativeInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new DialogueProtocolViolation(`${label} must be a non-negative integer`);
  }
  return value;
}

export function parseDialogueServerMessage(text) {
  if (typeof text !== "string") throw new DialogueProtocolViolation("server message must be text");
  let message;
  try {
    message = JSON.parse(text);
  } catch (error) {
    throw new DialogueProtocolViolation("server message is malformed JSON", { cause: error });
  }
  objectValue(message, "server message");
  if (message.version !== DIALOGUE_PROTOCOL_VERSION) throw new DialogueProtocolViolation("unsupported dialogue protocol version");
  if (!SERVER_TYPES.has(message.type)) throw new DialogueProtocolViolation("unknown dialogue message type");
  return message;
}

export function normalizeDialogueMessage(message) {
  if (message.type === "hello") {
    requiredString(message.connection_id, "connection_id");
    if (!["available", "offline"].includes(message.provider_state)) throw new DialogueProtocolViolation("invalid provider state");
    nonNegativeInteger(message.max_text_chars, "max_text_chars");
    return { kind: "hello", connectionId: message.connection_id, providerState: message.provider_state, maxTextChars: message.max_text_chars };
  }
  if (message.type === "history") {
    if (!Array.isArray(message.messages)) throw new DialogueProtocolViolation("history messages must be an array");
    const messages = message.messages.map((entry) => {
      objectValue(entry, "history entry");
      if (!["user", "assistant"].includes(entry.role)) throw new DialogueProtocolViolation("history role is invalid");
      return {
        messageId: requiredString(entry.message_id, "message_id"),
        clientTurnId: turnId(entry.client_turn_id),
        role: entry.role,
        segmentIndex: nonNegativeInteger(entry.segment_index, "segment_index"),
        text: requiredString(entry.text, "history text"),
        utcUnixNs: requiredString(entry.utc_unix_ns, "utc_unix_ns"),
      };
    });
    return { kind: "history", messages };
  }
  if (message.type === "proactive.stream") {
    // P4.10: strict server-only proactive assistant segment.  It is never
    // tied to a pending user turn; the model appends it independently and
    // there is no client_turn_id field on the wire, so this branch must run
    // before the turn-id extraction below.
    const proactiveId = requiredString(message.proactive_id, "proactive_id");
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(proactiveId)) throw new DialogueProtocolViolation("proactive_id is invalid");
    const segmentIndex = nonNegativeInteger(message.segment_index, "segment_index");
    const segmentCount = nonNegativeInteger(message.segment_count, "segment_count");
    if (segmentCount === 0 || segmentIndex >= segmentCount) throw new DialogueProtocolViolation("proactive segment boundary is invalid");
    const utcUnixNs = nonNegativeInteger(message.utc_unix_ns, "utc_unix_ns");
    const proactiveText = requiredString(message.text, "proactive text");
    if (proactiveText.trim().length === 0) throw new DialogueProtocolViolation("proactive text must not be blank");
    const proactive = {
      kind: "proactive",
      proactiveId,
      segmentIndex,
      segmentCount,
      text: proactiveText,
      utcUnixNs,
    };
    if (message.typewriter_ms !== undefined) {
      const value = message.typewriter_ms;
      if (!Number.isSafeInteger(value) || value <= 0 || value > 1000) throw new DialogueProtocolViolation("typewriter_ms must be a positive integer <= 1000");
      proactive.typewriterMs = value;
    }
    return proactive;
  }
  const clientTurnId = message.type === "turn.error" && message.client_turn_id === null
    ? null
    : turnId(message.client_turn_id);
  if (message.type === "turn.accepted") return { kind: "accepted", clientTurnId };
  function optionalTypewriterMs(value) {
    if (value === undefined) return null;
    if (!Number.isSafeInteger(value) || value <= 0 || value > 1000) throw new DialogueProtocolViolation("typewriter_ms must be a positive integer <= 1000");
    return value;
  }

  function optionalPlanId(value) {
    if (value === undefined) return null;
    if (typeof value !== "string" || value.length === 0 || value.length > 64) throw new DialogueProtocolViolation("plan_id must be a string of length 1..64");
    return value;
  }

  if (message.type === "turn.stream") {
    const segmentIndex = nonNegativeInteger(message.segment_index, "segment_index");
    const segmentCount = nonNegativeInteger(message.segment_count, "segment_count");
    if (segmentCount === 0 || segmentIndex >= segmentCount) throw new DialogueProtocolViolation("stream segment boundary is invalid");
    const stream = {
      kind: "stream", clientTurnId,
      segmentIndex,
      segmentCount,
      text: requiredString(message.text, "stream text"),
    };
    // Optional P3.8 receptor-plan fields: only attached when present in the
    // wire message so the P2.6 deepEqual contract stays backward compatible.
    if (message.typewriter_ms !== undefined) stream.typewriterMs = optionalTypewriterMs(message.typewriter_ms);
    if (message.plan_id !== undefined) stream.planId = optionalPlanId(message.plan_id);
    return stream;
  }
  if (message.type === "turn.completed") {
    if (typeof message.writer_applied !== "boolean") throw new DialogueProtocolViolation("writer_applied must be boolean");
    const completed = {
      kind: "completed", clientTurnId,
      segmentCount: nonNegativeInteger(message.segment_count, "segment_count"),
      writerApplied: message.writer_applied,
    };
    if (message.typewriter_ms !== undefined) completed.typewriterMs = optionalTypewriterMs(message.typewriter_ms);
    if (message.plan_id !== undefined) completed.planId = optionalPlanId(message.plan_id);
    return completed;
  }
  if (message.type === "turn.degraded") return { kind: "degraded", clientTurnId, reason: requiredString(message.reason, "reason") };
  if (message.type === "turn.cancelled") return { kind: "cancelled", clientTurnId };
  if (typeof message.fatal !== "boolean" || typeof message.retry !== "boolean") throw new DialogueProtocolViolation("error flags must be boolean");
  return { kind: "error", clientTurnId, code: requiredString(message.code, "error code"), detail: requiredString(message.detail, "error detail"), fatal: message.fatal, retry: message.retry };
}

export function dialogueWebSocketUrl(locationLike) {
  const protocol = locationLike.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${locationLike.host}/ws/dialogue`;
}

export function submitTurnMessage(clientTurnId, text) {
  if (typeof text !== "string") throw new DialogueProtocolViolation("turn text must be a string");
  const normalized = text.trim();
  if (normalized.length === 0 || normalized.length > MAX_DIALOGUE_TEXT_CHARS) throw new DialogueProtocolViolation("turn text length is invalid");
  return JSON.stringify({ version: DIALOGUE_PROTOCOL_VERSION, type: "turn.submit", client_turn_id: turnId(clientTurnId), text: normalized });
}

export function cancelTurnMessage(clientTurnId) {
  return JSON.stringify({ version: DIALOGUE_PROTOCOL_VERSION, type: "turn.cancel", client_turn_id: turnId(clientTurnId) });
}

export function typingSubmitMessage(clientTurnId, state) {
  if (!["start", "heartbeat", "stop"].includes(state)) throw new DialogueProtocolViolation("typing state must be start|heartbeat|stop");
  return JSON.stringify({ version: DIALOGUE_PROTOCOL_VERSION, type: "typing.submit", client_turn_id: turnId(clientTurnId), state });
}
