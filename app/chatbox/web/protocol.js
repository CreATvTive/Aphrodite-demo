export const TRAJECTORY_PROTOCOL_VERSION = "aphrodite.chatbox.trajectory-ws/1";

const SERVER_TYPES = new Set([
  "hello",
  "registry",
  "gate",
  "history_begin",
  "history_batch",
  "current",
  "history_end",
  "live",
  "error",
]);

export class ProtocolViolation extends Error {
  constructor(message) {
    super(message);
    this.name = "ProtocolViolation";
  }
}

function objectValue(value, label) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolViolation(`${label} must be an object`);
  }
  return value;
}

function exactKeys(value, keys, label) {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new ProtocolViolation(`${label} keys do not match the v1 contract`);
  }
}

function finiteNumber(value, label) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ProtocolViolation(`${label} must be finite`);
  }
  return value;
}

function nonNegativeInteger(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new ProtocolViolation(`${label} must be a non-negative safe integer`);
  }
  return value;
}

export function canonicalCursor(value, { nullable = false, label = "cursor" } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new ProtocolViolation(`${label} must be a canonical decimal string`);
  }
  return value;
}

function requiredString(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new ProtocolViolation(`${label} must be a non-empty string`);
  }
  return value;
}

export function parseServerMessage(text) {
  if (typeof text !== "string") throw new ProtocolViolation("server message must be text");
  let message;
  try {
    message = JSON.parse(text);
  } catch (error) {
    throw new ProtocolViolation("server message is malformed JSON", { cause: error });
  }
  objectValue(message, "server message");
  if (message.version !== TRAJECTORY_PROTOCOL_VERSION) {
    throw new ProtocolViolation("unsupported trajectory protocol version");
  }
  if (!SERVER_TYPES.has(message.type)) {
    throw new ProtocolViolation("unknown trajectory message type");
  }
  return message;
}

export function normalizeRegistry(message) {
  exactKeys(message, ["version", "type", "dimensions"], "registry");
  if (!Array.isArray(message.dimensions) || message.dimensions.length === 0) {
    throw new ProtocolViolation("registry dimensions must be a non-empty array");
  }
  const ids = new Set();
  return message.dimensions.map((entry, index) => {
    objectValue(entry, `registry dimension ${index}`);
    exactKeys(entry, ["ordinal", "dim_id", "temporary_name", "birth_time", "strength", "trigger_count"], `registry dimension ${index}`);
    if (entry.ordinal !== index) throw new ProtocolViolation("registry ordinal/order mismatch");
    const dimId = requiredString(entry.dim_id, "registry dim_id");
    if (ids.has(dimId)) throw new ProtocolViolation("registry contains duplicate dim_id");
    ids.add(dimId);
    return Object.freeze({
      ordinal: index,
      dimId,
      temporaryName: requiredString(entry.temporary_name, "temporary_name"),
      birthTime: finiteNumber(entry.birth_time, "birth_time"),
      strength: finiteNumber(entry.strength, "strength"),
      triggerCount: canonicalCursor(entry.trigger_count, { label: "trigger_count" }),
    });
  });
}

export function normalizeGate(message, registry) {
  exactKeys(message, ["version", "type", "gate_version", "mode", "temperature", "temperature_applied", "bandwidth", "weights"], "gate");
  requiredString(message.gate_version, "gate_version");
  requiredString(message.mode, "gate mode");
  finiteNumber(message.temperature, "gate temperature");
  if (typeof message.temperature_applied !== "boolean") throw new ProtocolViolation("temperature_applied must be boolean");
  nonNegativeInteger(message.bandwidth, "gate bandwidth");
  if (!Array.isArray(message.weights) || message.weights.length !== registry.length) {
    throw new ProtocolViolation("gate weights must match registry length");
  }
  const weights = message.weights.map((entry, index) => {
    objectValue(entry, `gate weight ${index}`);
    exactKeys(entry, ["ordinal", "dim_id", "weight"], `gate weight ${index}`);
    if (entry.ordinal !== index || entry.dim_id !== registry[index].dimId) {
      throw new ProtocolViolation("gate order must match registry order");
    }
    const weight = finiteNumber(entry.weight, "gate weight");
    if (weight < 0 || weight > 1) throw new ProtocolViolation("gate weight must be in [0, 1]");
    return Object.freeze({ ordinal: index, dimId: entry.dim_id, weight });
  });
  return Object.freeze({
    version: message.gate_version,
    mode: message.mode,
    temperature: message.temperature,
    temperatureApplied: message.temperature_applied,
    bandwidth: message.bandwidth,
    weights,
  });
}

function normalizeDimensions(dimensions, registry, label) {
  if (!Array.isArray(dimensions) || dimensions.length !== registry.length) {
    throw new ProtocolViolation(`${label} dimensions must match registry length`);
  }
  return dimensions.map((entry, index) => {
    objectValue(entry, `${label} dimension ${index}`);
    exactKeys(entry, ["ordinal", "dim_id", "value", "velocity", "attractor", "slow_baseline", "ou_acceleration"], `${label} dimension ${index}`);
    if (entry.ordinal !== index || entry.dim_id !== registry[index].dimId) {
      throw new ProtocolViolation(`${label} dimension order must match registry`);
    }
    return Object.freeze({
      ordinal: index,
      dimId: entry.dim_id,
      value: finiteNumber(entry.value, `${label} value`),
      velocity: finiteNumber(entry.velocity, `${label} velocity`),
      attractor: finiteNumber(entry.attractor, `${label} attractor`),
      slowBaseline: finiteNumber(entry.slow_baseline, `${label} slow_baseline`),
      ouAcceleration: finiteNumber(entry.ou_acceleration, `${label} ou_acceleration`),
    });
  });
}

export function normalizeFrame(frame, registry) {
  objectValue(frame, "trajectory frame");
  exactKeys(frame, ["cursor", "boot_id", "field_tick", "utc_unix_ns", "dimensions"], "trajectory frame");
  const utcUnixNs = canonicalCursor(frame.utc_unix_ns, { label: "utc_unix_ns" });
  return Object.freeze({
    cursor: canonicalCursor(frame.cursor),
    bootId: requiredString(frame.boot_id, "boot_id"),
    fieldTick: canonicalCursor(frame.field_tick, { label: "field_tick" }),
    utcUnixNs,
    timestampMs: Number(BigInt(utcUnixNs) / 1_000_000n),
    dimensions: normalizeDimensions(frame.dimensions, registry, "trajectory frame"),
  });
}

export function normalizeCurrent(message, registry) {
  exactKeys(message, ["version", "type", "field_tick", "dimensions"], "current");
  return Object.freeze({
    cursor: null,
    bootId: null,
    fieldTick: canonicalCursor(message.field_tick, { label: "field_tick" }),
    utcUnixNs: null,
    timestampMs: null,
    dimensions: normalizeDimensions(message.dimensions, registry, "current"),
  });
}

function normalizeHello(message) {
  exactKeys(message, ["version", "type", "connection_id", "head_cursor", "tick_interval_seconds", "initial_history_frames", "max_resume_frames", "history_batch_frames"], "hello");
  return Object.freeze({
    connectionId: requiredString(message.connection_id, "connection_id"),
    headCursor: canonicalCursor(message.head_cursor, { nullable: true, label: "head_cursor" }),
    tickIntervalSeconds: finiteNumber(message.tick_interval_seconds, "tick_interval_seconds"),
    initialHistoryFrames: nonNegativeInteger(message.initial_history_frames, "initial_history_frames"),
    maxResumeFrames: nonNegativeInteger(message.max_resume_frames, "max_resume_frames"),
    historyBatchFrames: nonNegativeInteger(message.history_batch_frames, "history_batch_frames"),
  });
}

function normalizeError(message) {
  exactKeys(message, ["version", "type", "code", "fatal", "retry", "detail"], "error");
  if (typeof message.fatal !== "boolean") throw new ProtocolViolation("error fatal must be boolean");
  if (!new Set(["none", "fresh", "later"]).has(message.retry)) throw new ProtocolViolation("unknown error retry directive");
  return Object.freeze({
    code: requiredString(message.code, "error code"),
    fatal: message.fatal,
    retry: message.retry,
    detail: requiredString(message.detail, "error detail"),
  });
}

export class TrajectoryProtocolSession {
  constructor(model, requestedCursor) {
    this.model = model;
    this.requestedCursor = canonicalCursor(requestedCursor, { nullable: true, label: "requested cursor" });
    this.phase = "hello";
    this.hello = null;
    this.receivedHistory = [];
  }

  accept(text) {
    const message = parseServerMessage(text);
    if (message.type === "error") return { kind: "server-error", error: normalizeError(message) };
    if (message.type === "hello") {
      this.#expect("hello", message.type);
      this.hello = normalizeHello(message);
      this.phase = "registry";
      return { kind: "hello", hello: this.hello };
    }
    if (message.type === "registry") {
      this.#expect("registry", message.type);
      const registry = normalizeRegistry(message);
      this.model.setRegistry(registry, { replace: this.requestedCursor === null });
      this.phase = "gate";
      return { kind: "registry", registry };
    }
    if (message.type === "gate") {
      this.#expect("gate", message.type);
      const gate = normalizeGate(message, this.model.registry);
      this.model.setGate(gate);
      this.phase = "history-begin";
      return { kind: "gate", gate };
    }
    if (message.type === "history_begin") {
      this.#expect("history-begin", message.type);
      exactKeys(message, ["version", "type", "mode", "after_cursor", "cutoff_cursor", "truncated_before"], "history_begin");
      const afterCursor = canonicalCursor(message.after_cursor, { nullable: true, label: "after_cursor" });
      const cutoffCursor = canonicalCursor(message.cutoff_cursor, { nullable: true, label: "cutoff_cursor" });
      const expectedMode = this.requestedCursor === null ? "tail" : "resume";
      if (message.mode !== expectedMode || afterCursor !== this.requestedCursor) {
        throw new ProtocolViolation("history mode/cursor does not match subscription");
      }
      if (typeof message.truncated_before !== "boolean") throw new ProtocolViolation("truncated_before must be boolean");
      this.receivedHistory = [];
      this.model.beginHistory({ mode: message.mode, truncatedBefore: message.truncated_before, cutoffCursor });
      this.phase = "history";
      return { kind: "history-begin" };
    }
    if (message.type === "history_batch") {
      this.#expect("history", message.type);
      exactKeys(message, ["version", "type", "frames"], "history_batch");
      if (!Array.isArray(message.frames)) throw new ProtocolViolation("history frames must be an array");
      const frames = message.frames.map((frame) => normalizeFrame(frame, this.model.registry));
      this.receivedHistory.push(...frames);
      const merge = this.model.mergeHistory(frames);
      return { kind: "history-batch", merge };
    }
    if (message.type === "current") {
      this.#expect("history", message.type);
      const current = normalizeCurrent(message, this.model.registry);
      this.model.setCurrent(current);
      return { kind: "current", current };
    }
    if (message.type === "history_end") {
      this.#expect("history", message.type);
      exactKeys(message, ["version", "type", "cutoff_cursor", "first_cursor", "last_cursor", "frame_count"], "history_end");
      const cutoffCursor = canonicalCursor(message.cutoff_cursor, { nullable: true, label: "cutoff_cursor" });
      const firstCursor = canonicalCursor(message.first_cursor, { nullable: true, label: "first_cursor" });
      const lastCursor = canonicalCursor(message.last_cursor, { nullable: true, label: "last_cursor" });
      nonNegativeInteger(message.frame_count, "frame_count");
      const expectedFirst = this.receivedHistory.at(0)?.cursor ?? null;
      const expectedLast = this.receivedHistory.at(-1)?.cursor ?? null;
      if (message.frame_count !== this.receivedHistory.length || firstCursor !== expectedFirst || lastCursor !== expectedLast) {
        throw new ProtocolViolation("history_end summary does not match received history");
      }
      this.model.endHistory({ cutoffCursor });
      this.phase = "live";
      return { kind: "synced", hello: this.hello };
    }
    if (message.type === "live") {
      this.#expect("live", message.type);
      exactKeys(message, ["version", "type", "frame"], "live");
      const frame = normalizeFrame(message.frame, this.model.registry);
      return { kind: "live", frame, merge: this.model.mergeLive(frame) };
    }
    throw new ProtocolViolation("unhandled trajectory message");
  }

  #expect(expected, received) {
    if (this.phase !== expected) {
      throw new ProtocolViolation(`received ${received} while awaiting ${this.phase}`);
    }
  }
}

export function trajectoryWebSocketUrl(locationLike) {
  const protocol = locationLike.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${locationLike.host}/ws/trajectory`;
}
