const TYPE_INTERVAL_MS = 22;

export class DialogueView {
  constructor(elements, model, { reducedMotion = false, onAnimationChange = () => {} } = {}) {
    this.elements = elements;
    this.model = model;
    this.reducedMotion = reducedMotion;
    this.onAnimationChange = onAnimationChange;
    this.animation = null;
    this.nodes = new Map();
  }

  renderHistory() {
    this.cancelAnimation();
    this.nodes.clear();
    this.elements.log.replaceChildren();
    for (const message of this.model.messages) this.#appendMessage(message, false);
    this.renderEmpty();
    this.scrollToEnd();
  }

  appendLatest({ animate = true } = {}) {
    const message = this.model.messages.at(-1);
    if (!message) return;
    this.#appendMessage(message, animate);
    this.renderEmpty();
    this.scrollToEnd();
  }

  updateMessage(clientTurnId, status) {
    const node = this.nodes.get(`${clientTurnId}:user:0`);
    if (node) node.dataset.status = status;
  }

  completeTurn(clientTurnId, status = "complete") {
    if (status !== "complete") this.cancelAnimation(true);
    for (const [key, node] of this.nodes) if (key.startsWith(`${clientTurnId}:`)) node.dataset.status = status;
  }

  get hasActiveAnimation() {
    return this.animation !== null;
  }

  renderEmpty() {
    this.elements.empty.hidden = this.model.messages.length !== 0;
  }

  setState(state, detail) {
    this.elements.state.dataset.state = state;
    this.elements.state.textContent = detail;
  }

  cancelAnimation(reveal = false) {
    if (!this.animation) return;
    clearInterval(this.animation.timer);
    if (reveal) this.animation.target.textContent = this.animation.fullText;
    this.animation = null;
    this.onAnimationChange(false);
  }

  scrollToEnd() {
    this.elements.log.scrollTop = this.elements.log.scrollHeight;
  }

  #appendMessage(message, animate) {
    const article = document.createElement("article");
    article.className = `message message-${message.role}`;
    article.dataset.status = message.status;
    article.dataset.segmentIndex = String(message.segmentIndex);
    article.setAttribute("aria-label", message.role === "user" ? "你说" : "Aphrodite 说");
    const body = document.createElement("p");
    body.className = "message-body";
    article.append(body);
    this.elements.log.append(article);
    this.nodes.set(`${message.clientTurnId}:${message.role}:${message.segmentIndex}`, article);
    if (animate && message.role === "assistant" && !this.reducedMotion) this.#typewrite(body, message.text, message.typewriterMs ?? null);
    else body.textContent = message.text;
  }

  #typewrite(target, fullText, intervalMs) {
    this.cancelAnimation(true);
    let offset = 0;
    target.textContent = "";
    // Use the server-planned per-character interval when provided; otherwise
    // fall back to the local default.  Reduced-motion clients render
    // immediately (handled by the caller).
    const interval = Number.isSafeInteger(intervalMs) && intervalMs > 0 && intervalMs <= 1000 ? intervalMs : TYPE_INTERVAL_MS;
    const timer = setInterval(() => {
      offset = Math.min(fullText.length, offset + 2);
      target.textContent = fullText.slice(0, offset);
      this.scrollToEnd();
      if (offset >= fullText.length) this.cancelAnimation();
    }, interval);
    this.animation = { timer, target, fullText };
    this.onAnimationChange(true);
  }
}
