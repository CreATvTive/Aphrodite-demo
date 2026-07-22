import assert from "node:assert/strict";
import test from "node:test";

import {
  TRAJECTORY_PROTOCOL_VERSION,
  ProtocolViolation,
  TrajectoryProtocolSession,
  trajectoryWebSocketUrl,
} from "../../app/chatbox/web/protocol.js";
import {
  MAX_BUFFERED_FRAMES,
  ReconnectBackoff,
  TrajectoryModel,
} from "../../app/chatbox/web/trajectory-model.js";
import { buildChartColumns } from "../../app/chatbox/web/trajectory-chart.js";

const wire = (type, fields = {}) => JSON.stringify({ version: TRAJECTORY_PROTOCOL_VERSION, type, ...fields });

function registryMessage(count) {
  return wire("registry", {
    dimensions: Array.from({ length: count }, (_, ordinal) => ({
      ordinal,
      dim_id: `dim-${ordinal}`,
      temporary_name: `维-${ordinal}`,
      birth_time: 17,
      strength: 1,
      trigger_count: String(ordinal),
    })),
  });
}

function gateMessage(count) {
  return wire("gate", {
    gate_version: "aphrodite.chatbox.expression-gate/1",
    mode: "v0_all_open",
    temperature: 1,
    temperature_applied: false,
    bandwidth: 4,
    weights: Array.from({ length: count }, (_, ordinal) => ({ ordinal, dim_id: `dim-${ordinal}`, weight: 1 })),
  });
}

function frame(cursor, count, { bootId = "boot-a", timestampMs = Number(cursor) * 1_000 } = {}) {
  return {
    cursor: String(cursor),
    boot_id: bootId,
    field_tick: String(cursor),
    utc_unix_ns: String(BigInt(timestampMs) * 1_000_000n),
    dimensions: Array.from({ length: count }, (_, ordinal) => ({
      ordinal,
      dim_id: `dim-${ordinal}`,
      value: Number(cursor) / 10 + ordinal,
      velocity: ordinal / 100,
      attractor: ordinal / 10,
      slow_baseline: -ordinal / 10,
      ou_acceleration: 0,
    })),
  };
}

function startSession(model, count, afterCursor = null) {
  const session = new TrajectoryProtocolSession(model, afterCursor);
  session.accept(wire("hello", {
    connection_id: "connection-id",
    head_cursor: afterCursor,
    tick_interval_seconds: 1,
    initial_history_frames: 900,
    max_resume_frames: 3600,
    history_batch_frames: 50,
  }));
  session.accept(registryMessage(count));
  session.accept(gateMessage(count));
  session.accept(wire("history_begin", {
    mode: afterCursor === null ? "tail" : "resume",
    after_cursor: afterCursor,
    cutoff_cursor: afterCursor,
    truncated_before: false,
  }));
  return session;
}

test("registry drives arbitrary dimension count and gate order", () => {
  for (const count of [1, 12, 17]) {
    const model = new TrajectoryModel();
    const session = startSession(model, count);
    session.accept(wire("history_end", {
      cutoff_cursor: null,
      first_cursor: null,
      last_cursor: null,
      frame_count: 0,
    }));
    assert.equal(model.registry.length, count);
    assert.deepEqual(model.gate.weights.map((weight) => weight.dimId), model.registry.map((dimension) => dimension.dimId));
  }
});

test("history, current, cursor holes, duplicates, live, and restart boots merge once", () => {
  const model = new TrajectoryModel();
  const session = startSession(model, 3);
  session.accept(wire("history_batch", { frames: [frame(1, 3), frame(3, 3)] }));
  session.accept(wire("history_end", {
    cutoff_cursor: "3",
    first_cursor: "1",
    last_cursor: "3",
    frame_count: 2,
  }));
  assert.deepEqual(model.frames().map((entry) => entry.cursor), ["1", "3"]);
  assert.equal(session.accept(wire("live", { frame: frame(3, 3) })).merge, "duplicate");
  assert.equal(session.accept(wire("live", { frame: frame(5, 3, { bootId: "boot-b", timestampMs: 5_000 }) })).merge, "appended");
  assert.equal(model.bootTransitionCount, 1);
  assert.equal(model.current.dimensions.length, 3);
});

test("resume retains trajectory while a fresh tail replaces it", () => {
  const model = new TrajectoryModel();
  let session = startSession(model, 2);
  session.accept(wire("history_batch", { frames: [frame(1, 2)] }));
  session.accept(wire("history_end", { cutoff_cursor: "1", first_cursor: "1", last_cursor: "1", frame_count: 1 }));

  session = startSession(model, 2, "1");
  session.accept(wire("history_batch", { frames: [frame(3, 2)] }));
  session.accept(wire("history_end", { cutoff_cursor: "3", first_cursor: "3", last_cursor: "3", frame_count: 1 }));
  assert.deepEqual(model.frames().map((entry) => entry.cursor), ["1", "3"]);

  session = startSession(model, 2, null);
  session.accept(wire("history_batch", { frames: [frame(8, 2)] }));
  session.accept(wire("history_end", { cutoff_cursor: "8", first_cursor: "8", last_cursor: "8", frame_count: 1 }));
  assert.deepEqual(model.frames().map((entry) => entry.cursor), ["8"]);
});

test("fresh resync directive is surfaced for fatal and non-fatal server errors", () => {
  const model = new TrajectoryModel();
  const session = new TrajectoryProtocolSession(model, "9");
  const result = session.accept(wire("error", { code: "resync_required", fatal: true, retry: "fresh", detail: "backlog" }));
  assert.equal(result.error.retry, "fresh");
  const nonFatal = session.accept(wire("error", { code: "resync_required", fatal: false, retry: "fresh", detail: "refresh" }));
  assert.equal(nonFatal.error.retry, "fresh");
  assert.equal(nonFatal.error.fatal, false);
});

test("protocol ordering is strict", () => {
  const model = new TrajectoryModel();
  const session = new TrajectoryProtocolSession(model, "9");
  assert.throws(() => session.accept(registryMessage(2)), ProtocolViolation);
});

test("ring buffer and chart conversion remain bounded for a multi-hour stream", () => {
  const model = new TrajectoryModel();
  const session = startSession(model, 2);
  const frames = Array.from({ length: MAX_BUFFERED_FRAMES + 800 }, (_, index) => frame(index + 1, 2));
  for (let start = 0; start < frames.length; start += 50) {
    model.mergeHistory(frames.slice(start, start + 50).map((entry) => ({
      cursor: entry.cursor,
      bootId: entry.boot_id,
      fieldTick: entry.field_tick,
      timestampMs: Number(BigInt(entry.utc_unix_ns) / 1_000_000n),
      utcUnixNs: entry.utc_unix_ns,
      dimensions: entry.dimensions.map((point) => ({
        ordinal: point.ordinal,
        dimId: point.dim_id,
        value: point.value,
        velocity: point.velocity,
        attractor: point.attractor,
        slowBaseline: point.slow_baseline,
        ouAcceleration: point.ou_acceleration,
      })),
    })));
  }
  assert.equal(model.frameCount, MAX_BUFFERED_FRAMES);
  assert.equal(model.frames().at(0).cursor, "801");
  const columns = buildChartColumns(model.frames(), 1, 320);
  assert.ok(columns.length <= 320);
  assert.equal(columns.at(-1).value.last, (MAX_BUFFERED_FRAMES + 800) / 10 + 1);
  void session;
});

test("exponential reconnect delay is deterministic, capped, and resettable", () => {
  const backoff = new ReconnectBackoff({ baseMs: 500, maximumMs: 2_000, jitter: 0, random: () => 0.5 });
  assert.deepEqual([backoff.nextDelayMs(), backoff.nextDelayMs(), backoff.nextDelayMs(), backoff.nextDelayMs()], [500, 1_000, 2_000, 2_000]);
  backoff.reset();
  assert.equal(backoff.nextDelayMs(), 500);
});

test("WebSocket URL follows page host and security scheme", () => {
  assert.equal(trajectoryWebSocketUrl({ protocol: "http:", host: "127.0.0.1:8765" }), "ws://127.0.0.1:8765/ws/trajectory");
  assert.equal(trajectoryWebSocketUrl({ protocol: "https:", host: "localhost:9443" }), "wss://localhost:9443/ws/trajectory");
});
