// P4.10 frontend contract: strict server-only proactive.stream frame.
//
// Covers:
//   - proactive.stream parses with strict fields and stable id
//   - empty text / out-of-bound segment / invalid id / bad utc_unix_ns are rejected
//   - DialogueModel.appendProactive appends without a pending user turn
//   - proactive segments survive history restore and reduced-motion rendering
//   - existing P2.6 turn-stream contract stays unchanged (regression guard)
//
// Run: node --test tests/chatbox/p4_10_frontend_contract.mjs

import assert from "node:assert/strict";
import test from "node:test";

import {
  DIALOGUE_PROTOCOL_VERSION,
  DialogueProtocolViolation,
  normalizeDialogueMessage,
  parseDialogueServerMessage,
} from "../../app/chatbox/web/dialogue-protocol.js";
import { DialogueModel } from "../../app/chatbox/web/dialogue-model.js";
import { DialogueView } from "../../app/chatbox/web/dialogue-view.js";

const wire = (type, fields = {}) =>
  JSON.stringify({ version: DIALOGUE_PROTOCOL_VERSION, type, ...fields });

test("proactive.stream normalizes with strict fields and stable proactive id", () => {
  const normalized = normalizeDialogueMessage(
    parseDialogueServerMessage(
      wire("proactive.stream", {
        proactive_id: "proactive-1",
        segment_index: 0,
        segment_count: 1,
        text: "你在吗",
        utc_unix_ns: 17,
      }),
    ),
  );
  assert.deepEqual(normalized, {
    kind: "proactive",
    proactiveId: "proactive-1",
    segmentIndex: 0,
    segmentCount: 1,
    text: "你在吗",
    utcUnixNs: 17,
  });
});

test("proactive.stream optional typewriter_ms is accepted when in range", () => {
  const normalized = normalizeDialogueMessage(
    parseDialogueServerMessage(
      wire("proactive.stream", {
        proactive_id: "proactive-2",
        segment_index: 0,
        segment_count: 2,
        text: "first",
        utc_unix_ns: 100,
        typewriter_ms: 80,
      }),
    ),
  );
  assert.equal(normalized.typewriterMs, 80);
});

test("proactive.stream rejects empty text, bad segment boundary, invalid id, and negative ns", () => {
  assert.throws(
    () =>
      normalizeDialogueMessage(
        parseDialogueServerMessage(
          wire("proactive.stream", {
            proactive_id: "proactive-x",
            segment_index: 0,
            segment_count: 1,
            text: "  ",
            utc_unix_ns: 1,
          }),
        ),
      ),
    DialogueProtocolViolation,
  );
  assert.throws(
    () =>
      normalizeDialogueMessage(
        parseDialogueServerMessage(
          wire("proactive.stream", {
            proactive_id: "proactive-x",
            segment_index: 2,
            segment_count: 2,
            text: "bad",
            utc_unix_ns: 1,
          }),
        ),
      ),
    DialogueProtocolViolation,
  );
  assert.throws(
    () =>
      normalizeDialogueMessage(
        parseDialogueServerMessage(
          wire("proactive.stream", {
            proactive_id: "bad id with space",
            segment_index: 0,
            segment_count: 1,
            text: "bad",
            utc_unix_ns: 1,
          }),
        ),
      ),
    DialogueProtocolViolation,
  );
  assert.throws(
    () =>
      normalizeDialogueMessage(
        parseDialogueServerMessage(
          wire("proactive.stream", {
            proactive_id: "proactive-x",
            segment_index: 0,
            segment_count: 1,
            text: "bad",
            utc_unix_ns: -1,
          }),
        ),
      ),
    DialogueProtocolViolation,
  );
});

test("model appends proactive segments without a pending user turn and keeps them complete", () => {
  const model = new DialogueModel();
  assert.equal(model.pendingTurn, null);
  model.appendProactive("proactive-1", 0, 2, "第一句", 17);
  model.appendProactive("proactive-1", 1, 2, "第二句", 18);
  assert.equal(model.pendingTurn, null);
  assert.deepEqual(
    model.messages.map((message) => message.role),
    ["assistant", "assistant"],
  );
  assert.ok(model.messages.every((message) => message.status === "complete"));
  assert.equal(model.messages[0].proactiveId, "proactive-1");
  assert.equal(model.messages[1].text, "第二句");
});

test("model rejects out-of-order or invalid proactive segment boundaries", () => {
  const model = new DialogueModel();
  assert.throws(() => model.appendProactive("proactive-1", 0, 0, "bad", 1));
  assert.throws(() => model.appendProactive("proactive-1", 1, 1, "bad", 1));
});

test("proactive segments coexist with reactive turns and do not mutate pending turn state", () => {
  const model = new DialogueModel();
  model.beginTurn("turn-1", "你好");
  model.acceptTurn("turn-1");
  model.streamSegment("turn-1", 0, 1, "回复");
  model.finishTurn("turn-1", "complete");
  assert.equal(model.pendingTurn, null);
  model.appendProactive("proactive-1", 0, 1, "主动一句", 99);
  assert.equal(model.pendingTurn, null);
  assert.deepEqual(
    model.messages.map((message) => message.role),
    ["user", "assistant", "assistant"],
  );
  assert.equal(model.messages.at(-1).proactiveId, "proactive-1");
});

test("proactive segments survive history restore as complete assistant messages", () => {
  const model = new DialogueModel();
  model.replaceHistory([
    {
      messageId: "1",
      clientTurnId: "turn-1",
      role: "user",
      segmentIndex: 0,
      text: "你好",
      utcUnixNs: "10",
    },
    {
      messageId: "2",
      clientTurnId: "turn-1",
      role: "assistant",
      segmentIndex: 0,
      text: "回复",
      utcUnixNs: "11",
    },
  ]);
  model.appendProactive("proactive-1", 0, 1, "主动一句", 99);
  assert.deepEqual(
    model.messages.map((message) => message.role),
    ["user", "assistant", "assistant"],
  );
  assert.ok(model.messages.every((message) => message.status === "complete"));
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
  append(child) {
    this.children.push(child);
  }
  replaceChildren(...children) {
    this.children = children;
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  get scrollHeight() {
    return this.children.length * 40;
  }
}

globalThis.document = { createElement: () => new TinyNode() };

function viewElements() {
  return { log: new TinyNode(), empty: new TinyNode(), state: new TinyNode() };
}

test("reduced motion renders proactive assistant segments immediately", () => {
  const model = new DialogueModel();
  const elements = viewElements();
  const view = new DialogueView(elements, model, { reducedMotion: true });
  model.appendProactive("proactive-1", 0, 1, "主动一句", 100);
  view.appendLatest({ animate: true });
  assert.equal(elements.log.children.length, 1);
  assert.equal(elements.log.children[0].children[0].textContent, "主动一句");
  assert.equal(view.hasActiveAnimation, false);
});

test("existing turn.stream contract stays unchanged (P2.6 regression guard)", () => {
  const stream = normalizeDialogueMessage(
    parseDialogueServerMessage(
      wire("turn.stream", {
        client_turn_id: "turn-1",
        segment_index: 1,
        segment_count: 2,
        text: "第二条",
      }),
    ),
  );
  assert.deepEqual(stream, {
    kind: "stream",
    clientTurnId: "turn-1",
    segmentIndex: 1,
    segmentCount: 2,
    text: "第二条",
  });
  assert.throws(
    () =>
      normalizeDialogueMessage(
        parseDialogueServerMessage(
          wire("turn.stream", {
            client_turn_id: "turn-1",
            segment_index: 2,
            segment_count: 2,
            text: "bad",
          }),
        ),
      ),
    DialogueProtocolViolation,
  );
});