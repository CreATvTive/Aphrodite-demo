import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  DIALOGUE_PROTOCOL_VERSION,
  DialogueProtocolViolation,
  MAX_DIALOGUE_TEXT_CHARS,
  cancelTurnMessage,
  dialogueWebSocketUrl,
  normalizeDialogueMessage,
  parseDialogueServerMessage,
  submitTurnMessage,
} from "../../app/chatbox/web/dialogue-protocol.js";
import { createClientTurnId, DialogueModel } from "../../app/chatbox/web/dialogue-model.js";
import { DialogueView } from "../../app/chatbox/web/dialogue-view.js";

const wire = (type, fields = {}) => JSON.stringify({ version: DIALOGUE_PROTOCOL_VERSION, type, ...fields });

test("dialogue protocol is independent, versioned, and same-origin", () => {
  assert.equal(DIALOGUE_PROTOCOL_VERSION, "aphrodite.chatbox.dialogue-ws/1");
  assert.equal(dialogueWebSocketUrl({ protocol: "http:", host: "127.0.0.1:8765" }), "ws://127.0.0.1:8765/ws/dialogue");
  assert.equal(dialogueWebSocketUrl({ protocol: "https:", host: "localhost:9443" }), "wss://localhost:9443/ws/dialogue");
  const submitted = JSON.parse(submitTurnMessage("turn-1", "  你好  "));
  assert.deepEqual(submitted, { version: DIALOGUE_PROTOCOL_VERSION, type: "turn.submit", client_turn_id: "turn-1", text: "你好" });
  assert.equal(JSON.parse(cancelTurnMessage("turn-1")).type, "turn.cancel");
  assert.throws(() => submitTurnMessage("bad id", "x"), DialogueProtocolViolation);
  assert.throws(() => submitTurnMessage("turn-empty", "  "), DialogueProtocolViolation);
  assert.throws(() => submitTurnMessage("turn-long", "x".repeat(MAX_DIALOGUE_TEXT_CHARS + 1)), DialogueProtocolViolation);
});

test("all dialogue lifecycle messages normalize with strict stream boundaries", () => {
  assert.deepEqual(normalizeDialogueMessage(parseDialogueServerMessage(wire("hello", {
    connection_id: "connection", provider_state: "available", max_text_chars: 4000,
  }))), { kind: "hello", connectionId: "connection", providerState: "available", maxTextChars: 4000 });
  const history = normalizeDialogueMessage(parseDialogueServerMessage(wire("history", { messages: [{
    message_id: "1", client_turn_id: "turn-1", role: "user", segment_index: 0,
    text: "你好", utc_unix_ns: "17",
  }] })));
  assert.equal(history.messages[0].text, "你好");
  assert.equal(normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.accepted", { client_turn_id: "turn-1" }))).kind, "accepted");
  const stream = normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.stream", {
    client_turn_id: "turn-1", segment_index: 1, segment_count: 2, text: "第二条",
  })));
  assert.deepEqual(stream, { kind: "stream", clientTurnId: "turn-1", segmentIndex: 1, segmentCount: 2, text: "第二条" });
  assert.equal(normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.completed", {
    client_turn_id: "turn-1", segment_count: 2, writer_applied: true,
  }))).kind, "completed");
  assert.equal(normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.degraded", {
    client_turn_id: "turn-1", reason: "provider_unavailable",
  }))).kind, "degraded");
  assert.equal(normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.cancelled", {
    client_turn_id: "turn-1",
  }))).kind, "cancelled");
  const error = normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.error", {
    client_turn_id: null, code: "invalid_message", detail: "bad", fatal: true, retry: false,
  })));
  assert.equal(error.kind, "error");
  assert.throws(() => normalizeDialogueMessage(parseDialogueServerMessage(wire("turn.stream", {
    client_turn_id: "turn-1", segment_index: 2, segment_count: 2, text: "bad",
  }))), DialogueProtocolViolation);
  assert.throws(() => parseDialogueServerMessage(wire("unknown")), DialogueProtocolViolation);
});

test("model serializes one pending turn, multiple assistant messages, and terminal states", () => {
  const model = new DialogueModel();
  model.beginTurn("turn-1", "你好");
  assert.throws(() => model.beginTurn("turn-2", "并发"));
  model.acceptTurn("turn-1");
  model.streamSegment("turn-1", 0, 2, "第一条");
  model.streamSegment("turn-1", 1, 2, "第二条");
  assert.deepEqual(model.messages.map((message) => message.role), ["user", "assistant", "assistant"]);
  assert.throws(() => model.streamSegment("turn-1", 1, 2, "乱序"));
  model.finishTurn("turn-1", "complete");
  assert.equal(model.pendingTurn, null);
  assert.ok(model.messages.every((message) => message.status === "complete"));

  model.beginTurn("turn-offline", "还在吗");
  model.acceptTurn("turn-offline");
  model.finishTurn("turn-offline", "degraded");
  assert.equal(model.messages.at(-1).status, "degraded");
});

class TinyNode {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.scrollTop = 0;
  }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes[name] = value; }
  get scrollHeight() { return this.children.length * 40; }
}

globalThis.document = { createElement: () => new TinyNode() };

function viewElements() {
  return { log: new TinyNode(), empty: new TinyNode(), state: new TinyNode() };
}

test("reduced motion reveals replies immediately and preserves message boundaries", () => {
  const model = new DialogueModel();
  const elements = viewElements();
  const view = new DialogueView(elements, model, { reducedMotion: true });
  model.beginTurn("turn-view", "你好");
  view.appendLatest({ animate: false });
  model.acceptTurn("turn-view");
  model.streamSegment("turn-view", 0, 2, "第一条回复");
  view.appendLatest({ animate: true });
  model.streamSegment("turn-view", 1, 2, "第二条回复");
  view.appendLatest({ animate: true });
  assert.equal(elements.log.children.length, 3);
  assert.equal(elements.log.children[1].children[0].textContent, "第一条回复");
  assert.equal(elements.log.children[2].children[0].textContent, "第二条回复");
  assert.equal(view.hasActiveAnimation, false);
});

test("typewriter can be cancelled by revealing the complete reply", () => {
  const model = new DialogueModel();
  const elements = viewElements();
  const animationStates = [];
  const view = new DialogueView(elements, model, {
    reducedMotion: false,
    onAnimationChange: (active) => animationStates.push(active),
  });
  model.beginTurn("turn-type", "说吧");
  model.acceptTurn("turn-type");
  model.streamSegment("turn-type", 0, 1, "一段需要逐字呈现的回复");
  view.appendLatest({ animate: true });
  assert.equal(view.hasActiveAnimation, true);
  view.cancelAnimation(true);
  assert.equal(view.hasActiveAnimation, false);
  assert.equal(elements.log.children[0].children[0].textContent, "一段需要逐字呈现的回复");
  assert.deepEqual(animationStates, [true, false]);
});

test("turn ids are protocol-safe and distinct inputs vary", () => {
  assert.match(createClientTurnId(17, 0.1), /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/);
  assert.notEqual(createClientTurnId(17, 0.1), createClientTurnId(18, 0.1));
});

test("page contract exposes keyboard, live regions, cancellation, and narrow responsive layout", async () => {
  const root = new URL("../../app/chatbox/web/", import.meta.url);
  const [html, css, app] = await Promise.all([
    readFile(new URL("index.html", root), "utf8"),
    readFile(new URL("styles.css", root), "utf8"),
    readFile(new URL("app.js", root), "utf8"),
  ]);
  assert.match(html, /role="log"/);
  assert.match(html, /aria-live="assertive"/);
  assert.match(html, /dialogue-cancel/);
  assert.match(html, /Enter 发送，Shift\+Enter 换行/);
  assert.match(app, /event\.key === "Enter" && !event\.shiftKey && !event\.isComposing/);
  assert.match(app, /turn\.degraded|result\.kind === "degraded"/);
  assert.match(app, /reconnecting/);
  assert.match(css, /@media \(max-width: 660px\)/);
  assert.match(css, /overflow-wrap: anywhere/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});
