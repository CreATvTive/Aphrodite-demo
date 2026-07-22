import { TRAJECTORY_PROTOCOL_VERSION, ProtocolViolation, TrajectoryProtocolSession, trajectoryWebSocketUrl } from "./protocol.js";
import { ReconnectBackoff, TrajectoryModel } from "./trajectory-model.js";
import { GateBars } from "./gate-bars.js";
import { TrajectoryBoard } from "./trajectory-chart.js";
import {
  DialogueProtocolViolation,
  cancelTurnMessage,
  dialogueWebSocketUrl,
  normalizeDialogueMessage,
  parseDialogueServerMessage,
  submitTurnMessage,
  typingSubmitMessage,
} from "./dialogue-protocol.js";
import { createClientTurnId, DialogueModel } from "./dialogue-model.js";
import { DialogueView } from "./dialogue-view.js";

const elements = {
  status: document.querySelector("#connection-status"),
  statusDetail: document.querySelector("#connection-detail"),
  freshness: document.querySelector("#freshness-status"),
  retry: document.querySelector("#retry-now"),
  protocol: document.querySelector("#protocol-version"),
  samples: document.querySelector("#sample-count"),
  duration: document.querySelector("#observed-duration"),
  cursor: document.querySelector("#latest-cursor"),
  tick: document.querySelector("#latest-tick"),
  boot: document.querySelector("#boot-id"),
  transitions: document.querySelector("#boot-transitions"),
  historyNotice: document.querySelector("#history-notice"),
  chartGrid: document.querySelector("#trajectory-grid"),
  gateList: document.querySelector("#gate-list"),
  gateMetadata: document.querySelector("#gate-metadata"),
};

const model = new TrajectoryModel();
const board = new TrajectoryBoard(elements.chartGrid, () => model.frames());
const gateBars = new GateBars(elements.gateList, elements.gateMetadata);
const backoff = new ReconnectBackoff();

let socket = null;
let session = null;
let retryTimer = null;
let generation = 0;
let stopped = false;
let retryDirective = "later";
let forceFresh = false;
let lastDataReceiptMs = null;
let tickIntervalMs = 1_000;
let connectionState = "initial";

elements.protocol.textContent = TRAJECTORY_PROTOCOL_VERSION;

function setConnectionState(state, detail) {
  connectionState = state;
  elements.status.dataset.state = state;
  const labels = {
    initial: "尚未连接",
    connecting: "正在连接",
    syncing: "正在归并历史",
    live: "实时",
    reconnecting: "等待重连",
    error: "连接错误",
    stopped: "已停止重试",
  };
  elements.status.textContent = labels[state] ?? state;
  elements.statusDetail.textContent = detail;
  updateFreshness();
}

function updateFreshness() {
  const age = lastDataReceiptMs === null ? Infinity : Date.now() - lastDataReceiptMs;
  const stale = connectionState !== "live" || age > Math.max(5_000, tickIntervalMs * 4);
  elements.freshness.dataset.stale = String(stale);
  elements.freshness.textContent = stale ? "数据陈旧 / 不代表场冻结" : `数据新鲜 · ${Math.max(0, Math.round(age / 1_000))} 秒前`;
}

function updateMetrics() {
  const current = model.current;
  elements.samples.textContent = model.frameCount.toLocaleString("zh-CN");
  elements.duration.textContent = formatDuration(model.observedDurationMs);
  elements.cursor.textContent = model.lastCursor ?? "—";
  elements.tick.textContent = current?.fieldTick ?? "—";
  elements.boot.textContent = current?.bootId ? current.bootId.slice(0, 12) : "—";
  elements.transitions.textContent = String(model.bootTransitionCount);
  elements.historyNotice.hidden = !model.truncatedBefore;
}

function formatDuration(milliseconds) {
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1_000);
  return minutes > 0 ? `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒` : `${seconds} 秒`;
}

function renderSynchronizedState() {
  board.setRegistry(model.registry);
  gateBars.setRegistry(model.registry);
  gateBars.update(model.gate);
  board.reset(model.frames(), model.current);
  updateMetrics();
}

function connect() {
  if (stopped) return;
  clearTimeout(retryTimer);
  retryTimer = null;
  const currentGeneration = ++generation;
  const requestedCursor = forceFresh ? null : model.lastCursor;
  retryDirective = "later";
  session = new TrajectoryProtocolSession(model, requestedCursor);
  setConnectionState(backoff.attempt > 0 ? "reconnecting" : "connecting", requestedCursor === null ? "请求初始历史" : `从游标 ${requestedCursor} 续接`);
  socket = new WebSocket(trajectoryWebSocketUrl(window.location));

  socket.addEventListener("open", () => {
    if (currentGeneration !== generation) return;
    setConnectionState("syncing", requestedCursor === null ? "加载最近历史" : "补齐断线期间轨迹");
    socket.send(JSON.stringify({
      version: TRAJECTORY_PROTOCOL_VERSION,
      type: "subscribe",
      after_cursor: requestedCursor,
    }));
  });

  socket.addEventListener("message", (event) => {
    if (currentGeneration !== generation) return;
    try {
      const result = session.accept(event.data);
      if (result.kind === "registry") {
        board.setRegistry(model.registry);
        gateBars.setRegistry(model.registry);
      } else if (result.kind === "gate") {
        gateBars.update(model.gate);
      } else if (result.kind === "server-error") {
        handleServerError(result.error);
      } else if (result.kind === "synced") {
        tickIntervalMs = Math.max(250, result.hello.tickIntervalSeconds * 1_000);
        forceFresh = false;
        backoff.reset();
        lastDataReceiptMs = model.current ? Date.now() : null;
        renderSynchronizedState();
        setConnectionState("live", `连接 ${result.hello.connectionId.slice(0, 8)} · 实时接收中`);
      } else if (result.kind === "live" && result.merge === "appended") {
        lastDataReceiptMs = Date.now();
        board.append(result.frame);
        updateMetrics();
        updateFreshness();
      }
    } catch (error) {
      const detail = error instanceof ProtocolViolation ? error.message : "客户端无法归并轨迹消息";
      forceFresh = true;
      retryDirective = "later";
      setConnectionState("error", `协议错误：${detail}`);
      socket.close(1002, "protocol_violation");
    }
  });

  socket.addEventListener("error", () => {
    if (currentGeneration === generation) setConnectionState("error", "WebSocket 无法连接；轨迹已标记为陈旧");
  });

  socket.addEventListener("close", () => {
    if (currentGeneration !== generation || stopped) return;
    socket = null;
    if (retryDirective === "none") {
      stopped = true;
      setConnectionState("stopped", "服务端拒绝自动恢复；可手动重试");
      return;
    }
    scheduleReconnect();
  });
}

function handleServerError(error) {
  retryDirective = error.retry;
  if (error.retry === "fresh") forceFresh = true;
  setConnectionState("error", `服务端 ${error.code}：${error.detail}`);
  if (error.fatal || error.retry === "fresh") socket.close(1000, "server_error_received");
}

function scheduleReconnect() {
  const delay = backoff.nextDelayMs();
  setConnectionState("reconnecting", `${(delay / 1_000).toFixed(1)} 秒后自动重连${forceFresh ? "（全量重同步）" : "（游标续接）"}`);
  retryTimer = setTimeout(connect, delay);
}

function reconnectNow() {
  stopped = false;
  retryDirective = "later";
  clearTimeout(retryTimer);
  retryTimer = null;
  generation += 1;
  if (socket) socket.close(1000, "manual_reconnect");
  socket = null;
  connect();
}

elements.retry.addEventListener("click", reconnectNow);
window.addEventListener("online", reconnectNow);
window.addEventListener("beforeunload", () => {
  stopped = true;
  clearTimeout(retryTimer);
  socket?.close(1000, "page_unload");
});

setInterval(updateFreshness, 1_000);
connect();

const dialogueElements = {
  state: document.querySelector("#dialogue-status"),
  retry: document.querySelector("#dialogue-retry"),
  empty: document.querySelector("#dialogue-empty"),
  log: document.querySelector("#message-history"),
  form: document.querySelector("#dialogue-form"),
  input: document.querySelector("#dialogue-input"),
  count: document.querySelector("#dialogue-count"),
  limit: document.querySelector("#dialogue-limit"),
  send: document.querySelector("#dialogue-send"),
  cancel: document.querySelector("#dialogue-cancel"),
  live: document.querySelector("#dialogue-live-status"),
};
const dialogueModel = new DialogueModel();
const dialogueView = new DialogueView(dialogueElements, dialogueModel, {
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  onAnimationChange: () => updateComposer(),
});
const dialogueBackoff = new ReconnectBackoff({ baseMs: 650, maximumMs: 10_000, jitter: 0.15 });
let dialogueSocket = null;
let dialogueRetryTimer = null;
let dialogueGeneration = 0;
let dialogueStopped = false;
// P3.7 typing state: client sends start/heartbeat/stop with throttling.
let typingActive = false;
let typingHeartbeatTimer = null;
const TYPING_HEARTBEAT_INTERVAL_MS = 3000;
const TYPING_THROTTLE_MS = 1200;
let typingLastSentMs = 0;
let typingTurnId = null;

function updateComposer() {
  const length = dialogueElements.input.value.length;
  dialogueElements.count.textContent = String(length);
  dialogueElements.send.disabled = dialogueModel.connectionState !== "live" || Boolean(dialogueModel.pendingTurn) || length === 0 || length > dialogueModel.maxTextChars;
  dialogueElements.input.disabled = dialogueModel.connectionState !== "live" || Boolean(dialogueModel.pendingTurn);
  const canCancel = Boolean(dialogueModel.pendingTurn) || dialogueView.hasActiveAnimation;
  dialogueElements.cancel.hidden = !canCancel;
  dialogueElements.cancel.disabled = !canCancel;
  dialogueElements.cancel.textContent = dialogueModel.pendingTurn ? "停止等待" : "立即显示完整回复";
}

function setDialogueState(state, detail) {
  dialogueModel.connectionState = state;
  dialogueView.setState(state, detail);
  updateComposer();
}

function handleDialogueMessage(event) {
  const result = normalizeDialogueMessage(parseDialogueServerMessage(event.data));
  if (result.kind === "hello") {
    dialogueModel.providerState = result.providerState;
    dialogueModel.maxTextChars = result.maxTextChars;
    dialogueElements.input.maxLength = result.maxTextChars;
    dialogueElements.limit.textContent = String(result.maxTextChars);
    setDialogueState("syncing", "同步历史");
  } else if (result.kind === "history") {
    dialogueModel.replaceHistory(result.messages);
    dialogueView.renderHistory();
    dialogueBackoff.reset();
    setDialogueState("live", dialogueModel.providerState === "offline" ? "已连接 · 模型离线" : "已连接");
    if (dialogueModel.providerState === "offline") dialogueElements.live.textContent = "模型不可用；消息仍会保存，场动力学继续运行。";
  } else if (result.kind === "accepted") {
    dialogueModel.acceptTurn(result.clientTurnId);
    dialogueView.updateMessage(result.clientTurnId, "complete");
    dialogueElements.live.textContent = "消息已接收，正在等待回复。";
  } else if (result.kind === "stream") {
    dialogueModel.streamSegment(result.clientTurnId, result.segmentIndex, result.segmentCount, result.text);
    dialogueView.appendLatest({ animate: true });
    dialogueElements.live.textContent = `收到第 ${result.segmentIndex + 1} 条回复。`;
  } else if (result.kind === "completed") {
    dialogueModel.finishTurn(result.clientTurnId, "complete");
    dialogueView.completeTurn(result.clientTurnId);
    dialogueElements.live.textContent = "回复完成。";
    updateComposer();
    dialogueElements.input.focus();
  } else if (result.kind === "degraded") {
    dialogueModel.finishTurn(result.clientTurnId, "degraded");
    dialogueView.completeTurn(result.clientTurnId, "degraded");
    dialogueElements.live.textContent = "模型暂不可用；这轮没有生成回复，也没有写入增量。";
    updateComposer();
    dialogueElements.input.focus();
  } else if (result.kind === "cancelled") {
    dialogueModel.finishTurn(result.clientTurnId, "cancelled");
    dialogueView.completeTurn(result.clientTurnId, "cancelled");
    dialogueElements.live.textContent = "已停止等待这轮回复。";
    updateComposer();
    dialogueElements.input.focus();
  } else if (result.kind === "proactive") {
    // P4.10: server-only proactive assistant segment, independent of any
    // pending user turn.  Append and render with the existing assistant
    // presentation + reduced-motion semantics.
    dialogueModel.appendProactive(
      result.proactiveId,
      result.segmentIndex,
      result.segmentCount,
      result.text,
      result.utcUnixNs,
      result.typewriterMs ?? null,
    );
    dialogueView.appendLatest({ animate: true });
    dialogueElements.live.textContent = "Aphrodite 主动说了一句。";
    updateComposer();
  } else if (result.kind === "error") {
    if (result.clientTurnId && dialogueModel.pendingTurn?.clientTurnId === result.clientTurnId) {
      dialogueModel.finishTurn(result.clientTurnId, "failed");
      dialogueView.completeTurn(result.clientTurnId, "failed");
    }
    dialogueElements.live.textContent = `未完成：${result.detail}`;
    updateComposer();
    if (result.fatal) dialogueSocket?.close(1000, "fatal_dialogue_error");
  }
}

function connectDialogue() {
  if (dialogueStopped) return;
  clearTimeout(dialogueRetryTimer);
  const currentGeneration = ++dialogueGeneration;
  setDialogueState(dialogueBackoff.attempt ? "reconnecting" : "connecting", dialogueBackoff.attempt ? "正在重新连接" : "正在连接");
  dialogueSocket = new WebSocket(dialogueWebSocketUrl(window.location));
  dialogueSocket.addEventListener("message", (event) => {
    if (currentGeneration !== dialogueGeneration) return;
    try {
      handleDialogueMessage(event);
    } catch (error) {
      const detail = error instanceof DialogueProtocolViolation ? error.message : "无法处理对话消息";
      dialogueElements.live.textContent = `对话协议错误：${detail}`;
      dialogueSocket.close(1002, "dialogue_protocol_violation");
    }
  });
  dialogueSocket.addEventListener("error", () => {
    if (currentGeneration === dialogueGeneration) setDialogueState("error", "连接失败");
  });
  dialogueSocket.addEventListener("close", () => {
    if (currentGeneration !== dialogueGeneration || dialogueStopped) return;
    dialogueSocket = null;
    if (dialogueModel.pendingTurn) {
      dialogueModel.finishTurn(dialogueModel.pendingTurn.clientTurnId, "disconnected");
      dialogueElements.live.textContent = "连接中断；已接收的消息仍由服务端去重。";
    }
    const delay = dialogueBackoff.nextDelayMs();
    setDialogueState("reconnecting", `${(delay / 1000).toFixed(1)} 秒后重连`);
    dialogueRetryTimer = setTimeout(connectDialogue, delay);
  });
}

function reconnectDialogue() {
  dialogueStopped = false;
  clearTimeout(dialogueRetryTimer);
  dialogueGeneration += 1;
  dialogueSocket?.close(1000, "manual_reconnect");
  dialogueSocket = null;
  connectDialogue();
}

function sendTyping(state) {
  if (dialogueSocket?.readyState !== WebSocket.OPEN) return;
  if (!typingTurnId) return;
  const now = Date.now();
  if (state === "heartbeat" && now - typingLastSentMs < TYPING_THROTTLE_MS) return;
  try {
    dialogueSocket.send(typingSubmitMessage(typingTurnId, state));
    typingLastSentMs = now;
  } catch { /* protocol violation: ignore typing send failure */ }
}

function startTyping() {
  if (typingActive) return;
  typingActive = true;
  if (!typingTurnId) typingTurnId = createClientTurnId();
  sendTyping("start");
  clearInterval(typingHeartbeatTimer);
  typingHeartbeatTimer = setInterval(() => {
    if (typingActive) sendTyping("heartbeat");
  }, TYPING_HEARTBEAT_INTERVAL_MS);
}

function stopTyping() {
  if (!typingActive) return;
  typingActive = false;
  clearInterval(typingHeartbeatTimer);
  typingHeartbeatTimer = null;
  sendTyping("stop");
}

function submitDialogue(event) {
  event.preventDefault();
  const text = dialogueElements.input.value.trim();
  if (!text || dialogueModel.pendingTurn || dialogueSocket?.readyState !== WebSocket.OPEN) return;
  stopTyping();
  const clientTurnId = createClientTurnId();
  dialogueModel.beginTurn(clientTurnId, text);
  dialogueView.appendLatest({ animate: false });
  dialogueElements.input.value = "";
  dialogueSocket.send(submitTurnMessage(clientTurnId, text));
  dialogueElements.live.textContent = "正在发送。";
  updateComposer();
}

dialogueElements.form.addEventListener("submit", submitDialogue);
dialogueElements.input.addEventListener("input", () => {
  updateComposer();
  if (dialogueModel.connectionState !== "live" || dialogueModel.pendingTurn) return;
  if (dialogueElements.input.value.trim().length > 0) startTyping();
  else stopTyping();
});
dialogueElements.input.addEventListener("blur", stopTyping);
dialogueElements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    dialogueElements.form.requestSubmit();
  }
});
dialogueElements.cancel.addEventListener("click", () => {
  if (!dialogueModel.pendingTurn && dialogueView.hasActiveAnimation) {
    dialogueView.cancelAnimation(true);
    dialogueElements.live.textContent = "已显示完整回复。";
    updateComposer();
    return;
  }
  if (dialogueModel.pendingTurn && dialogueSocket?.readyState === WebSocket.OPEN) {
    dialogueSocket.send(cancelTurnMessage(dialogueModel.pendingTurn.clientTurnId));
    dialogueElements.cancel.disabled = true;
    dialogueView.cancelAnimation(true);
    dialogueElements.live.textContent = "正在停止等待。";
  }
});
dialogueElements.retry.addEventListener("click", reconnectDialogue);
window.addEventListener("online", reconnectDialogue);
window.addEventListener("beforeunload", () => {
  dialogueStopped = true;
  clearTimeout(dialogueRetryTimer);
  dialogueSocket?.close(1000, "page_unload");
});
updateComposer();
dialogueView.renderEmpty();
connectDialogue();
