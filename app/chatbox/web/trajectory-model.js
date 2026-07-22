export const MAX_BUFFERED_FRAMES = 7_200;

export class RingBuffer {
  constructor(capacity = MAX_BUFFERED_FRAMES) {
    if (!Number.isInteger(capacity) || capacity <= 0) throw new TypeError("capacity must be positive");
    this.capacity = capacity;
    this.storage = new Array(capacity);
    this.start = 0;
    this.length = 0;
  }

  clear() {
    this.storage = new Array(this.capacity);
    this.start = 0;
    this.length = 0;
  }

  push(value) {
    if (this.length < this.capacity) {
      this.storage[(this.start + this.length) % this.capacity] = value;
      this.length += 1;
      return;
    }
    this.storage[this.start] = value;
    this.start = (this.start + 1) % this.capacity;
  }

  at(index) {
    const normalized = index < 0 ? this.length + index : index;
    if (normalized < 0 || normalized >= this.length) return undefined;
    return this.storage[(this.start + normalized) % this.capacity];
  }

  toArray() {
    return Array.from({ length: this.length }, (_, index) => this.at(index));
  }
}

function sameRegistry(left, right) {
  return left.length === right.length && left.every((entry, index) => (
    entry.ordinal === right[index].ordinal
    && entry.dimId === right[index].dimId
    && entry.temporaryName === right[index].temporaryName
  ));
}

export class TrajectoryModel {
  constructor({ capacity = MAX_BUFFERED_FRAMES } = {}) {
    this.buffer = new RingBuffer(capacity);
    this.registry = [];
    this.gate = null;
    this.current = null;
    this.lastCursor = null;
    this.truncatedBefore = false;
    this.historyActive = false;
    this.bootTransitionCount = 0;
  }

  setRegistry(registry, { replace = false } = {}) {
    if (this.registry.length && !sameRegistry(this.registry, registry)) {
      if (!replace) throw new Error("registry changed during resume");
      this.clearTrajectory();
    }
    this.registry = registry;
  }

  setGate(gate) {
    this.gate = gate;
  }

  beginHistory({ mode, truncatedBefore }) {
    if (mode === "tail") this.clearTrajectory();
    this.truncatedBefore = Boolean(truncatedBefore);
    this.historyActive = true;
  }

  mergeHistory(frames) {
    let appended = 0;
    let duplicates = 0;
    for (const frame of frames) {
      const result = this.#mergeFrame(frame);
      if (result === "appended") appended += 1;
      else duplicates += 1;
    }
    return { appended, duplicates };
  }

  setCurrent(current) {
    if (this.buffer.length === 0) this.current = current;
  }

  endHistory() {
    this.historyActive = false;
    const latest = this.buffer.at(-1);
    if (latest) this.current = latest;
  }

  mergeLive(frame) {
    return this.#mergeFrame(frame);
  }

  #mergeFrame(frame) {
    if (this.lastCursor !== null && BigInt(frame.cursor) <= BigInt(this.lastCursor)) {
      return "duplicate";
    }
    const previous = this.buffer.at(-1);
    if (previous && previous.bootId !== frame.bootId) this.bootTransitionCount += 1;
    this.buffer.push(frame);
    this.lastCursor = frame.cursor;
    this.current = frame;
    return "appended";
  }

  clearTrajectory() {
    this.buffer.clear();
    this.current = null;
    this.lastCursor = null;
    this.bootTransitionCount = 0;
    this.truncatedBefore = false;
  }

  frames() {
    return this.buffer.toArray();
  }

  get frameCount() {
    return this.buffer.length;
  }

  get observedDurationMs() {
    const first = this.buffer.at(0);
    const last = this.buffer.at(-1);
    return first && last ? Math.max(0, last.timestampMs - first.timestampMs) : 0;
  }
}

export class ReconnectBackoff {
  constructor({ baseMs = 500, maximumMs = 30_000, jitter = 0.2, random = Math.random } = {}) {
    this.baseMs = baseMs;
    this.maximumMs = maximumMs;
    this.jitter = jitter;
    this.random = random;
    this.attempt = 0;
  }

  nextDelayMs() {
    const unjittered = Math.min(this.maximumMs, this.baseMs * (2 ** this.attempt));
    this.attempt += 1;
    const factor = 1 + ((this.random() * 2 - 1) * this.jitter);
    return Math.round(unjittered * factor);
  }

  reset() {
    this.attempt = 0;
  }
}
