export const CHART_WINDOW_MS = 60 * 60 * 1_000;

export function dimensionColor(index, total) {
  const spacing = total > 0 ? 300 / total : 0;
  const hue = (328 + index * spacing) % 360;
  return `hsl(${hue.toFixed(1)} 72% 68%)`;
}

function emptySeries() {
  return { min: Infinity, max: -Infinity, last: 0 };
}

function addValue(series, value) {
  series.min = Math.min(series.min, value);
  series.max = Math.max(series.max, value);
  series.last = value;
}

export function buildChartColumns(frames, ordinal, width, windowMs = CHART_WINDOW_MS) {
  if (!Number.isInteger(width) || width <= 0 || frames.length === 0) return [];
  const anchor = frames.at(-1).timestampMs;
  const beginning = anchor - windowMs;
  const columns = new Map();
  for (const frame of frames) {
    if (frame.timestampMs < beginning || frame.timestampMs > anchor) continue;
    const point = frame.dimensions[ordinal];
    if (!point) continue;
    const x = Math.min(width - 1, Math.max(0, Math.floor(((frame.timestampMs - beginning) / windowMs) * (width - 1))));
    let column = columns.get(x);
    if (!column) {
      column = { x, timestampMs: frame.timestampMs, value: emptySeries(), attractor: emptySeries(), baseline: emptySeries() };
      columns.set(x, column);
    }
    column.timestampMs = frame.timestampMs;
    addValue(column.value, point.value);
    addValue(column.attractor, point.attractor);
    addValue(column.baseline, point.slowBaseline);
  }
  return [...columns.values()].sort((left, right) => left.x - right.x);
}

function niceScale(frames, ordinal) {
  let maximum = 0;
  for (const frame of frames) {
    const point = frame.dimensions[ordinal];
    if (!point) continue;
    maximum = Math.max(maximum, Math.abs(point.value), Math.abs(point.attractor), Math.abs(point.slowBaseline));
  }
  maximum = Math.max(maximum * 1.15, 0.01);
  const power = 10 ** Math.floor(Math.log10(maximum));
  const normalized = maximum / power;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * power;
}

function seriesY(value, scale, height) {
  return Math.round((0.5 - value / (2 * scale)) * (height - 1));
}

class Sparkline {
  constructor(canvas, ordinal, color) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d", { alpha: true });
    this.ordinal = ordinal;
    this.color = color;
    this.scale = 0.01;
    this.lastTimestampMs = null;
    this.lastPoint = null;
    this.pendingShift = 0;
    this.bucket = null;
  }

  resize() {
    const ratio = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const rect = this.canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (this.canvas.width === width && this.canvas.height === height) return false;
    this.canvas.width = width;
    this.canvas.height = height;
    return true;
  }

  reset(frames) {
    this.resize();
    const { width, height } = this.canvas;
    this.context.clearRect(0, 0, width, height);
    this.scale = niceScale(frames, this.ordinal);
    const columns = buildChartColumns(frames, this.ordinal, width);
    this.#drawSeries(columns, "baseline", "rgba(203, 213, 225, 0.42)", Math.max(1, width / 900), [4, 5]);
    this.#drawSeries(columns, "attractor", "rgba(244, 114, 182, 0.62)", Math.max(1, width / 800), [7, 5]);
    this.#drawSeries(columns, "value", this.color, Math.max(1.5, width / 650), []);
    const latest = frames.at(-1);
    this.lastTimestampMs = latest?.timestampMs ?? null;
    this.lastPoint = latest?.dimensions[this.ordinal] ?? null;
    this.pendingShift = 0;
    this.bucket = null;
    return this.scale;
  }

  append(frame) {
    const point = frame.dimensions[this.ordinal];
    if (!point) return false;
    const largest = Math.max(Math.abs(point.value), Math.abs(point.attractor), Math.abs(point.slowBaseline));
    if (largest > this.scale * 0.96 || this.lastTimestampMs === null) return false;
    const { width, height } = this.canvas;
    const elapsed = Math.max(0, frame.timestampMs - this.lastTimestampMs);
    this.pendingShift += (elapsed / CHART_WINDOW_MS) * width;
    const shift = Math.floor(this.pendingShift);
    if (shift >= width) {
      this.context.clearRect(0, 0, width, height);
      this.pendingShift = 0;
      this.bucket = null;
    } else if (shift > 0) {
      this.context.drawImage(this.canvas, shift, 0, width - shift, height, 0, 0, width - shift, height);
      this.context.clearRect(width - shift, 0, shift, height);
      this.pendingShift -= shift;
      this.bucket = null;
    }
    if (!this.bucket) {
      this.bucket = { value: emptySeries(), attractor: emptySeries(), baseline: emptySeries() };
    }
    addValue(this.bucket.value, point.value);
    addValue(this.bucket.attractor, point.attractor);
    addValue(this.bucket.baseline, point.slowBaseline);
    const stripe = Math.max(2, Math.ceil(width / 700));
    this.context.clearRect(width - stripe, 0, stripe, height);
    this.#drawLiveBucket(this.bucket, width - 1, height);
    if (shift > 0 && elapsed <= 3_500 && this.lastPoint) {
      this.#connectLivePoint(this.lastPoint, point, Math.max(0, width - shift - 1), width - 1, height);
    }
    this.lastTimestampMs = frame.timestampMs;
    this.lastPoint = point;
    return true;
  }

  #drawSeries(columns, key, strokeStyle, lineWidth, dash) {
    const context = this.context;
    context.save();
    context.strokeStyle = strokeStyle;
    context.lineWidth = lineWidth;
    context.setLineDash(dash);
    let previous = null;
    for (const column of columns) {
      const series = column[key];
      const y = seriesY(series.last, this.scale, this.canvas.height);
      context.beginPath();
      context.moveTo(column.x, seriesY(series.min, this.scale, this.canvas.height));
      context.lineTo(column.x, seriesY(series.max, this.scale, this.canvas.height));
      context.stroke();
      if (previous && column.timestampMs - previous.timestampMs <= 3_500) {
        context.beginPath();
        context.moveTo(previous.x, previous.y);
        context.lineTo(column.x, y);
        context.stroke();
      }
      previous = { x: column.x, y, timestampMs: column.timestampMs };
    }
    context.restore();
  }

  #drawLiveBucket(bucket, x, height) {
    const seriesStyles = [
      ["baseline", "rgba(203, 213, 225, 0.42)", 1],
      ["attractor", "rgba(244, 114, 182, 0.62)", 1],
      ["value", this.color, 2],
    ];
    for (const [key, color, lineWidth] of seriesStyles) {
      const series = bucket[key];
      this.context.beginPath();
      this.context.strokeStyle = color;
      this.context.lineWidth = lineWidth;
      this.context.moveTo(x, seriesY(series.min, this.scale, height));
      this.context.lineTo(x, seriesY(series.max, this.scale, height));
      this.context.stroke();
    }
  }

  #connectLivePoint(previous, current, fromX, toX, height) {
    const seriesStyles = [
      ["slowBaseline", "rgba(203, 213, 225, 0.42)", 1],
      ["attractor", "rgba(244, 114, 182, 0.62)", 1],
      ["value", this.color, 2],
    ];
    for (const [key, color, lineWidth] of seriesStyles) {
      this.context.beginPath();
      this.context.strokeStyle = color;
      this.context.lineWidth = lineWidth;
      this.context.moveTo(fromX, seriesY(previous[key], this.scale, height));
      this.context.lineTo(toX, seriesY(current[key], this.scale, height));
      this.context.stroke();
    }
  }
}

export class TrajectoryBoard {
  constructor(root, frameProvider) {
    this.root = root;
    this.frameProvider = frameProvider;
    this.registry = [];
    this.cards = [];
    this.resizeFrame = null;
    this.resizeObserver = new ResizeObserver(() => {
      cancelAnimationFrame(this.resizeFrame);
      this.resizeFrame = requestAnimationFrame(() => this.reset(this.frameProvider()));
    });
    this.resizeObserver.observe(root);
  }

  setRegistry(registry) {
    const unchanged = this.registry.length === registry.length && this.registry.every((entry, index) => entry.dimId === registry[index].dimId);
    if (unchanged) return;
    this.registry = registry;
    this.root.replaceChildren();
    this.cards = registry.map((dimension, index) => this.#createCard(dimension, index));
  }

  #createCard(dimension, index) {
    const color = dimensionColor(index, this.registry.length);
    const article = document.createElement("article");
    article.className = "trajectory-card";
    article.innerHTML = `
      <header class="dimension-heading">
        <span class="dimension-swatch" aria-hidden="true"></span>
        <span><strong></strong><small></small></span>
        <output class="dimension-value" aria-label="当前值">—</output>
      </header>
      <div class="chart-frame">
        <canvas role="img"></canvas>
        <span class="scale-label" aria-hidden="true"></span>
      </div>
      <footer><span>−60 分钟</span><span class="velocity">速度 —</span><span>现在</span></footer>`;
    article.querySelector(".dimension-swatch").style.background = color;
    article.querySelector("strong").textContent = dimension.temporaryName;
    article.querySelector("small").textContent = dimension.dimId;
    const canvas = article.querySelector("canvas");
    canvas.setAttribute("aria-label", `${dimension.temporaryName}（${dimension.dimId}）最近一小时状态、吸引子与慢基线轨迹`);
    canvas.textContent = `${dimension.temporaryName} 轨迹图`;
    this.root.append(article);
    return {
      article,
      value: article.querySelector(".dimension-value"),
      velocity: article.querySelector(".velocity"),
      scale: article.querySelector(".scale-label"),
      chart: new Sparkline(canvas, index, color),
    };
  }

  reset(frames, current = frames.at(-1) ?? null) {
    if (!this.cards.length) return;
    for (const card of this.cards) {
      const scale = card.chart.reset(frames);
      card.scale.textContent = `±${formatNumber(scale)}`;
    }
    this.updateCurrent(current);
  }

  append(frame) {
    let incremental = true;
    for (const card of this.cards) incremental = card.chart.append(frame) && incremental;
    if (!incremental) this.reset(this.frameProvider(), frame);
    else this.updateCurrent(frame);
  }

  updateCurrent(frame) {
    this.cards.forEach((card, index) => {
      const point = frame?.dimensions[index];
      card.value.textContent = point ? signedNumber(point.value) : "—";
      card.velocity.textContent = point ? `速度 ${signedNumber(point.velocity)}` : "速度 —";
    });
  }
}

export function formatNumber(value) {
  const absolute = Math.abs(value);
  if (absolute !== 0 && absolute < 0.001) return value.toExponential(2);
  return value.toFixed(3);
}

function signedNumber(value) {
  const rendered = formatNumber(value);
  return value > 0 ? `+${rendered}` : rendered;
}
