/** Session telemetry, playback receipts, tool counts and the dashboard sheet.
 * Tool-call cards live in toolcalls.js; Harness traces have their own panel. */
import { t, tf } from "./i18n.js?v=20261027-duopra-r9-personal";
import { Sheet } from "./motion.js?v=20261027-duopra-r9-personal";

const $ = (id) => document.getElementById(id);
const desktop = matchMedia("(min-width: 1100px)");
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const percentile = (values, p) =>
  values.length
    ? [...values].sort((a, b) => a - b)[Math.max(0, Math.ceil(values.length * p) - 1)]
    : null;
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const clock = (seconds) =>
  `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
export class AgencyPanel {
  constructor() {
    this.open = false;
    this.connected = false;
    this.phase = "idle";
    this.units = 0;
    this.fedSeconds = 0;
    this.lastUtterance = null;
    this.lastMedia = null;
    this.startedAt = 0;
    this.loadedAt = Date.now();
    this.queueSeconds = 0;
    this.lastQueueRender = 0;
    this.receipts = new Map();
    this.receiptSamples = [];
    this.rows = 0;
    this.toolActive = 0;
    this.toolTotal = 0;
    this.lastTiming = -Infinity;
    this.tab = "capabilities";
    this.panel = $("agency-panel");
    this.logView = $("agency-log");
    this.toggle = $("agency-toggle");
    this.scrim = $("agency-scrim");
    this.ticks = [...$("hud-ticks").children];
    this.sheet = new Sheet(this.panel, {
      handle: $("agency-handle"),
      axis: () => (desktop.matches ? "x" : "y"),
      closedOffset: () =>
        desktop.matches ? this.panel.offsetWidth + 48 : this.panel.offsetHeight + 24,
      onProgress: (progress) => {
        const scrim = desktop.matches ? 0 : progress;
        this.scrim.style.opacity = scrim.toFixed(3);
        this.scrim.style.pointerEvents = scrim > 0.4 ? "auto" : "none";
      },
      onOpenChange: (open) => {
        this.open = open;
        this.toggle.setAttribute("aria-expanded", String(open));
        document.body.classList.toggle("panel-open", open);
      },
    });
    this.toggle.onclick = () => this.show(!this.open);
    this.tabs = [...document.querySelectorAll(".sheet-tab")];
    this.indicator = document.querySelector(".tab-indicator");
    for (const tab of this.tabs) tab.onclick = () => this.showTab(tab.dataset.tab);
    $("hud-actions-cell").onclick = () => this.openTab("runtime");
    $("close-agency").onclick = () => this.show(false);
    this.scrim.onclick = () => this.show(false);
    $("clear-log").onclick = () => this.clearLog();
    desktop.addEventListener("change", () => this.sheet.relayout());
    window.addEventListener("duplex-language", () => {
      this.renderCounts();
      this.renderStats();
      this.moveIndicator();
    });
    window.addEventListener("resize", () => this.moveIndicator());
    document.fonts?.ready.then(() => this.moveIndicator());
    this.moveIndicator();
    setInterval(() => this.updateTime(), 1000);
    this.renderStats();
    this.renderPhase();
  }
  show(open) {
    if (open) this.moveIndicator();
    this.sheet.setOpen(open);
  }
  openTab(name) {
    this.showTab(name);
    this.show(true);
  }
  showTab(name) {
    if (name === this.tab) return;
    const order = ["capabilities", "runtime", "personal"];
    const direction = order.indexOf(name) > order.indexOf(this.tab) ? 1 : -1;
    this.tab = name;
    for (const tab of this.tabs) {
      const selected = tab.dataset.tab === name;
      tab.classList.toggle("selected", selected);
      tab.setAttribute("aria-selected", String(selected));
    }
    for (const pane of document.querySelectorAll(".sheet-pane")) {
      const selected = pane.dataset.pane === name;
      pane.hidden = !selected;
      if (!selected) continue;
      // The incoming pane slides in from the side of the tab that was chosen.
      pane.style.setProperty("--dir", String(direction));
      pane.classList.remove("entering");
      void pane.offsetWidth;
      pane.classList.add("entering");
    }
    $("agency-body").scrollTop = 0;
    this.moveIndicator();
  }
  moveIndicator() {
    const selected = this.tabs.find((tab) => tab.dataset.tab === this.tab);
    if (!selected || !this.indicator) return;
    const first = !this.indicator.classList.contains("ready");
    if (first) this.indicator.style.transition = "none";
    this.indicator.style.transform = `translateX(${selected.offsetLeft}px)`;
    this.indicator.style.width = `${selected.offsetWidth}px`;
    if (first && selected.offsetWidth) {
      void this.indicator.offsetWidth;
      this.indicator.style.transition = "";
      this.indicator.classList.add("ready");
    }
  }

  /** One pulse of the dashboard button when something new starts. */
  pulse() {
    this.toggle.classList.remove("pulse");
    void this.toggle.offsetWidth;
    this.toggle.classList.add("pulse");
  }
  /** Tool calls are actions too: they join the badges and the HUD count. */
  setToolCounts(active, total) {
    this.toolActive = active;
    this.toolTotal = total;
    this.renderCounts();
  }
  renderCounts() {
    const active = this.toolActive, total = this.toolTotal;
    for (const id of ["agency-count", "tab-runtime-count"]) {
      $(id).textContent = String(active || total);
      $(id).hidden = !total;
    }
    $("hud-actions").textContent = String(active);
  }
  clear() {
    this.toolActive = 0;
    this.toolTotal = 0;
    this.renderCounts();
  }
  end() {
    this.endSession();
  }
  updateTime() {
    $("session-clock").textContent = this.connected
      ? clock(Math.max(0, (Date.now() - this.startedAt) / 1000))
      : "00:00";
  }

  // ----- telemetry -----------------------------------------------------------
  setPhase(phase) {
    if (phase === this.phase) return;
    this.phase = phase;
    if (phase === "connecting") this.event("sys", t("logConnecting"));
    this.renderPhase();
  }
  renderPhase() {
    const label = t("phase_" + this.phase);
    $("hud-state").textContent = label;
    $("stat-state").textContent = label;
    $("hud").dataset.phase = this.phase;
  }
  unitSent() {
    this.units++;
    const tick = this.ticks[(this.units - 1) % this.ticks.length];
    tick.classList.remove("on");
    void tick.offsetWidth;
    tick.classList.add("on");
    $("hud-units").textContent = String(this.units).padStart(4, "0");
    $("stat-units").textContent = String(this.units).padStart(4, "0");
  }
  setQueue(player) {
    if (!player?.context) return;
    const now = performance.now();
    if (now - this.lastQueueRender < 250) return;
    this.lastQueueRender = now;
    const seconds = Math.max(0, player.cursor - player.context.currentTime) + player.pendingSeconds;
    $("stat-queue").textContent = `${seconds.toFixed(2)} s`;
  }
  playbackReceived(message) {
    if (message.utterance_id !== this.lastUtterance) {
      this.lastUtterance = message.utterance_id;
      // Utterance ids are long compound tokens; the tail is enough to tell turns apart.
      const id = String(message.utterance_id || "").split(":").pop().slice(0, 8);
      this.event("out", tf("logSpeak", { id }));
    }
    this.receipts.set(`${message.utterance_id}:${message.chunk_seq}`, performance.now());
    if (this.receipts.size > 256) this.receipts.delete(this.receipts.keys().next().value);
  }
  playbackAck(message) {
    const key = `${message.utterance_id}:${message.chunks}`;
    const received = this.receipts.get(key);
    if (received !== undefined) {
      const latency = performance.now() - received;
      this.receipts.delete(key);
      this.receiptSamples.push(latency);
      this.receiptSamples = this.receiptSamples.slice(-256);
      message.client_received_to_playback_end_ms = Math.round(latency);
      this.renderStats();
    }
    return message;
  }
  renderStats() {
    const p50 = percentile(this.receiptSamples, 0.5),
      p95 = percentile(this.receiptSamples, 0.95);
    $("stat-receipt").textContent = p50 === null ? "—" : `${Math.round(p50)} / ${Math.round(p95)} ms`;
    $("stat-units").textContent = String(this.units).padStart(4, "0");
    this.renderPhase();
  }

  // ----- session lifecycle ----------------------------------------------------
  setSession(ready) {
    this.connected = true;
    // Client time, so this clock agrees with the one in the top bar.
    this.startedAt = Date.now();
    this.units = 0;
    this.fedSeconds = 0;
    this.lastUtterance = null;
    this.lastMedia = null;
    this.receipts.clear();
    this.receiptSamples = [];
    for (const tick of this.ticks) tick.classList.remove("on");
    this.clearLog();
    this.event("sys", tf("logReady", { model: ready.model || "Duopra" }));
    this.renderStats();
  }
  endSession() {
    if (!this.connected) return;
    this.connected = false;
    this.event("sys", t("logEnded"));
    this.updateTime();
  }
  /** Messages the panel understands; returns true when it consumed one. */
  handle(message) {
    switch (message.type) {
      case "timing":
      case "telemetry": {
        // Native runtimes time every unit; one line every few seconds keeps the log readable.
        const now = performance.now();
        if (now - this.lastTiming < 5000) return true;
        this.lastTiming = now;
        const metrics = message.metrics || message;
        this.event(
          "sys",
          finite(metrics.compute_ms)
            ? tf("logTiming", {
                unit: metrics.tick ?? "—",
                compute: Math.round(metrics.compute_ms),
                rtf: finite(metrics.rtf_unit) ? metrics.rtf_unit.toFixed(2) : "—",
                queue: Math.round(metrics.queue_ms ?? 0),
              })
            : JSON.stringify(metrics).slice(0, 160),
        );
        return true;
      }
      case "media_status":
        // Feeding progress arrives every second; log only state changes.
        if (message.state !== this.lastMedia) {
          this.lastMedia = message.state;
          this.event("in", tf("logMedia", { state: message.state, seconds: message.seconds ?? 0 }));
        }
        // Uploaded audio is fed by the server one second at a time; those are
        // input units too, so count them the way live microphone units are counted.
        if (finite(message.seconds))
          while (this.fedSeconds < Math.floor(message.seconds)) {
            this.fedSeconds++;
            this.unitSent();
          }
        return false;
      case "command_error":
        this.event("err", tf("logError", { error: message.error }));
        return false;
      case "playback_cancel":
        this.event("out", tf("logCancelled", { reason: message.reason || "model_interrupt" }));
        return false;
      default:
        return false;
    }
  }

  // ----- stream log -------------------------------------------------------------
  event(tag, text) {
    const empty = this.logView.querySelector(".empty-copy");
    if (empty) empty.remove();
    const row = element("div", `log-row log-${tag}`);
    const origin = this.connected ? this.startedAt : this.loadedAt;
    const seconds = Math.max(0, (Date.now() - origin) / 1000);
    row.append(
      element("time", null, `+${clock(seconds)}.${Math.floor((seconds % 1) * 10)}`),
      element("b", null, tag.toUpperCase()),
      element("span", null, text),
    );
    this.logView.prepend(row);
    if (++this.rows > 80) {
      this.logView.lastElementChild?.remove();
      this.rows--;
    }
  }
  clearLog() {
    this.rows = 0;
    const copy = element("p", "empty-copy small", t("logEmpty"));
    copy.dataset.i18n = "logEmpty";
    this.logView.replaceChildren(copy);
  }
}
