export class DialogueModel {
  constructor() {
    this.messages = [];
    this.pendingTurn = null;
    this.connectionState = "initial";
    this.providerState = "offline";
    this.maxTextChars = 4000;
  }

  replaceHistory(messages) {
    this.messages = messages.map((message) => ({ ...message, status: "complete" }));
  }

  beginTurn(clientTurnId, text) {
    if (this.pendingTurn) throw new Error("a turn is already pending");
    this.pendingTurn = { clientTurnId, status: "sending", segments: [] };
    this.messages.push({ clientTurnId, role: "user", segmentIndex: 0, text, status: "pending" });
  }

  acceptTurn(clientTurnId) {
    this.#expectPending(clientTurnId);
    this.pendingTurn.status = "accepted";
    const user = this.#findLast((message) => message.clientTurnId === clientTurnId && message.role === "user");
    if (user) user.status = "complete";
  }

  streamSegment(clientTurnId, segmentIndex, segmentCount, text) {
    this.#expectPending(clientTurnId);
    if (segmentIndex !== this.pendingTurn.segments.length || segmentCount <= segmentIndex) throw new Error("stream segment order is invalid");
    const segment = { clientTurnId, role: "assistant", segmentIndex, segmentCount, text, status: "streaming" };
    this.pendingTurn.status = "streaming";
    this.pendingTurn.segments.push(segment);
    this.messages.push(segment);
  }

  finishTurn(clientTurnId, status) {
    this.#expectPending(clientTurnId);
    for (const message of this.messages) {
      if (message.clientTurnId !== clientTurnId) continue;
      if (status !== "complete" || message.status !== "complete") message.status = status;
    }
    this.pendingTurn = null;
  }

  failUnacceptedTurn(clientTurnId) {
    const index = this.#findLastIndex((message) => message.clientTurnId === clientTurnId && message.role === "user" && message.status === "pending");
    if (index >= 0) this.messages[index].status = "failed";
    this.pendingTurn = null;
  }

  appendProactive(proactiveId, segmentIndex, segmentCount, text, utcUnixNs, typewriterMs = null) {
    // P4.10: a server-only proactive assistant segment, independent of any
    // pending user turn.  It is appended directly to the message list and
    // marked complete; it never interacts with pendingTurn.
    if (!Number.isSafeInteger(segmentIndex) || !Number.isSafeInteger(segmentCount) || segmentCount <= 0 || segmentIndex >= segmentCount) {
      throw new Error("proactive segment boundary is invalid");
    }
    const segment = {
      proactiveId,
      role: "assistant",
      segmentIndex,
      segmentCount,
      text,
      status: "complete",
      utcUnixNs,
    };
    if (Number.isSafeInteger(typewriterMs) && typewriterMs > 0 && typewriterMs <= 1000) segment.typewriterMs = typewriterMs;
    this.messages.push(segment);
  }

  #findLast(predicate) {
    const index = this.#findLastIndex(predicate);
    return index < 0 ? undefined : this.messages[index];
  }

  #findLastIndex(predicate) {
    for (let index = this.messages.length - 1; index >= 0; index -= 1) {
      if (predicate(this.messages[index])) return index;
    }
    return -1;
  }

  #expectPending(clientTurnId) {
    if (!this.pendingTurn || this.pendingTurn.clientTurnId !== clientTurnId) throw new Error("server turn does not match pending turn");
  }
}

export function createClientTurnId(now = Date.now(), random = Math.random()) {
  return `turn-${now.toString(36)}-${Math.floor(random * 0x1000000).toString(36).padStart(5, "0")}`;
}
