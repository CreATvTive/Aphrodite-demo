import { dimensionColor } from "./trajectory-chart.js";

export class GateBars {
  constructor(root, metadataRoot) {
    this.root = root;
    this.metadataRoot = metadataRoot;
    this.registry = [];
    this.rows = [];
  }

  setRegistry(registry) {
    const unchanged = this.registry.length === registry.length && this.registry.every((entry, index) => entry.dimId === registry[index].dimId);
    if (unchanged) return;
    this.registry = registry;
    this.root.replaceChildren();
    this.rows = registry.map((dimension, index) => {
      const row = document.createElement("li");
      row.className = "gate-row";
      const color = dimensionColor(index, registry.length);
      row.innerHTML = `
        <span class="gate-label"><i aria-hidden="true"></i><span></span></span>
        <span class="gate-track" role="meter" aria-valuemin="0" aria-valuemax="1" aria-valuenow="0"><span></span></span>
        <output>—</output>`;
      row.querySelector("i").style.background = color;
      row.querySelector(".gate-label span").textContent = dimension.temporaryName;
      row.querySelector(".gate-track > span").style.background = color;
      this.root.append(row);
      return row;
    });
  }

  update(gate) {
    this.rows.forEach((row, index) => {
      const weight = gate.weights[index].weight;
      const meter = row.querySelector(".gate-track");
      meter.setAttribute("aria-valuenow", String(weight));
      meter.setAttribute("aria-label", `${this.registry[index].temporaryName} 门控权重 ${weight.toFixed(3)}`);
      row.querySelector(".gate-track > span").style.transform = `scaleX(${weight})`;
      row.querySelector("output").textContent = weight.toFixed(3);
    });
    this.metadataRoot.textContent = `${gate.mode} · k=${gate.bandwidth} · T=${gate.temperature.toFixed(2)}${gate.temperatureApplied ? "（已应用）" : "（调平）"}`;
  }
}
