/** Tool calls from native Duopra runtimes, one card per call. A card follows the
 * runtime's own events — the search the model ran, the call it made, what came back
 * and from where, when the result reached the model, and what it said next — and a
 * pill on the stage mirrors the call in flight. The checks listed are the ones the
 * runtime enforces before such an event can exist; nothing is inferred or simulated.
 * The result area uses a tool-specific module from toolviews.js where one exists. */
import { t, tf, getLanguage } from "./i18n.js?v=20261027-duopra-r9-personal";
import {
  toolTitle,
  domainIcon,
  fieldRows,
  headline,
  detailRows,
  groundingTerms,
  sourceLabel,
  rejectionText,
  routerErrorText,
  formatValue,
} from "./toolformat.js?v=20261027-duopra-r9-personal";
import { createToolViews } from "./toolviews.js?v=20261027-duopra-r9-personal";

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const STEPS = ["find", "call", "return", "inject", "answer"];
const IN_FLIGHT = new Set(["awaiting", "calling", "returned", "delivered", "answering"]);
// An utterance that began this long before a search or call is its acknowledgement.
const ACK_WINDOW_MS = 10000;
// The first utterance this many units after a result reached the model is its answer.
const ANSWER_WINDOW_UNITS = 12;
// The pill holds an in-flight step at least this long so a 0 ms call still reads as a step;
// the cards always update at once.
const PILL_DWELL_MS = 650;
const views = createToolViews({ t, tf, getLanguage, sourceLabel, formatValue });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("icon");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const counted = (key, count) => (count === 1 ? t(key + "One") : tf(key, { count }));
const unitLabel = (value) => (Number.isInteger(value) && value >= 0 ? `U${value}` : "");
const clip = (text, length) => (text.length > length ? text.slice(0, length - 1) + "…" : text);
const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const tone = (state) =>
  IN_FLIGHT.has(state)
    ? "progress"
    : state === "answered" || state === "consumed"
      ? "ok"
      : state === "failed"
        ? "bad"
        : state === "rejected"
          ? "warn"
          : "muted";
/** Arguments on one line, e.g. 城市=伦敦 · 日期=10月21日. */
const argumentLine = (args) =>
  fieldRows(args)
    .map((row) => `${row.label}=${row.value}`)
    .join(" · ");
/** A boundary-aware pattern, so 18 does not light up inside 2018 nor 十七 inside 二十七. */
function termPattern(terms) {
  const numeral = "零一二三四五六七八九十百千";
  return new RegExp(
    terms
      .map((term) => {
        let source = escapeRegExp(term);
        if (/^[\d.]/.test(term)) source = `(?<![\\d.])${source}`;
        if (/[\d.]$/.test(term)) source = `${source}(?![\\d.])`;
        if (numeral.includes(term[0])) source = `(?<![${numeral}])${source}`;
        if (numeral.includes(term.at(-1))) source = `${source}(?![${numeral}])`;
        return source;
      })
      .join("|"),
    "g",
  );
}
/** What the model said after the result reached it. */
const answerText = (record) =>
  record.answer ? (record.answer === record.ack ? record.answer.text.slice(record.answerFrom) : record.answer.text) : "";
/** Event copies for the raw view: schemas and hashes trimmed, everything else verbatim. */
function slim(event) {
  const copy = { ...event };
  delete copy.session_id;
  if (Array.isArray(copy.candidates))
    copy.candidates = copy.candidates.map((hit) => ({ name: hit.name, score: hit.score, matched_terms: hit.matched_terms }));
  if (copy.payload) {
    copy.payload = { ...copy.payload };
    for (const key of ["registry_sha256", "fixture_key"])
      if (typeof copy.payload[key] === "string") copy.payload[key] = copy.payload[key].slice(0, 12) + "…";
  }
  return copy;
}

export class ToolCalls {
  /** info(name) describes a tool from the capability board: {spec, domain, domainKey}. */
  constructor({ log, counts, pulse, open, info, usage, connected }) {
    Object.assign(this, { log, counts, pulse, open, info, usage, connected });
    this.list = $("call-list");
    this.records = new Map(); // key → record
    this.byCall = new Map(); // call_id → record (a search and its call share one record)
    this.nodes = new Map(); // key → card
    this.utterances = new Map();
    this.used = new Map();
    this.activity = new LiveActivity($("live-activity"), $("live-activity-status"), (key) => this.open(key));
    this.reset();
    // Tool-specific result modules; false keeps every card on the generic result box.
    this.modules = true;
    window.addEventListener("duplex-language", () => this.renderAll());
    window.addEventListener("duplex-theme", () => views.repaint());
    // The empty state says whether this runtime has tools at all, which the board learns later.
    window.addEventListener("duplex-capabilities", () => {
      if (!this.records.size) this.renderAll();
    });
    this.renderAll();
  }
  reset() {
    this.records.clear();
    this.byCall.clear();
    this.nodes.clear();
    this.utterances.clear();
    this.used.clear();
    // What the model can call right now and how it got there: "preloaded" or a search's key.
    this.visible = { names: new Set(), via: null };
    this.offered = [];
    this.hotNames = new Set();
    this.lastSeq = 0;
    this.serial = 0;
    this.live = false;
  }
  get activeCount() {
    let count = 0;
    for (const record of this.records.values()) if (IN_FLIGHT.has(record.state)) count++;
    return count;
  }

  // ----- session lifecycle ----------------------------------------------------
  clear() {
    this.reset();
    this.activity.hide(true);
    this.usage(new Map());
    this.renderAll();
  }
  setSession(ready) {
    this.clear();
    this.live = true;
    this.readState(ready.tool_state);
  }
  end() {
    if (!this.live) return;
    this.live = false;
    for (const record of this.records.values()) {
      if (record.state === "awaiting") record.state = "unused";
      else if (IN_FLIGHT.has(record.state)) record.state = "ended";
    }
    this.activity.hide();
    this.renderAll();
  }

  // ----- messages -------------------------------------------------------------------
  /** Consumes tool messages and returns true; speech is watched but never consumed. */
  handle(message) {
    switch (message.type) {
      case "tool_event":
        this.event(message.event || message);
        return true;
      case "tool_state":
        this.readState(message.state || message);
        return true;
      case "tools_candidates":
        this.log(
          "tool",
          tf("logManualFind", {
            names: (message.candidates || []).map((hit) => hit.name || hit.schema?.name).join(", ") || "—",
          }),
        );
        return true;
      case "playback":
      case "text":
        this.speech(message);
        return false;
      case "playback_end":
        this.speechEnded(message.utterance_id, false);
        return false;
      case "playback_cancel":
        this.speechEnded(message.utterance_id, true);
        return false;
      default:
        return false;
    }
  }
  readState(state) {
    if (!state || !Array.isArray(state.visible_tools)) return;
    this.visible.names = new Set(state.visible_tools.map((tool) => (typeof tool === "string" ? tool : tool?.name)));
    if (Array.isArray(state.hot_tools)) this.hotNames = new Set(state.hot_tools.map(tool => typeof tool === "string" ? tool : tool.name));
  }
  event(e) {
    const kind = e.type === "tool_event" ? e.phase : e.type;
    if (finite(e.seq)) {
      if (e.seq <= this.lastSeq) return;
      this.lastSeq = e.seq;
    } else if (kind === "rejected") {
      // The router rejects malformed spans before the tool runtime sees them.
      this.rejected({ code: "router", message: e.error, unit_index: e.tick, router: true });
      return;
    }
    switch (kind) {
      case "session_open":
        this.visible = { names: new Set(e.tools || []), via: "preloaded" };
        this.hotNames = new Set(e.tools || []);
        break;
      case "authorization":
        this.log("sys", t(e.personal_authorized ? "logAuthOn" : "logAuthOff"));
        break;
      case "tools_exposed":
        this.offered = e.tools || [];
        break;
      case "selected":
        this.selected(e);
        break;
      case "submitted":
        this.submitted(e);
        break;
      case "completed":
        this.completed(e);
        break;
      case "failed":
        this.failed(e);
        break;
      case "injected":
        this.injected(e);
        break;
      case "rejected":
        this.rejected(e);
        break;
      case "cancelled":
      case "stale_discarded":
        this.settle(e, kind === "cancelled" ? "cancelled" : "stale");
        break;
      case "generation_cancelled":
        this.generationCancelled(e);
        break;
      case "manual_result":
        this.log("tool", `${t("callManual")} · ${clip(JSON.stringify(e.value ?? ""), 80)}`);
        break;
    }
  }

  // ----- the call lifecycle -------------------------------------------------------
  create(callId, fields) {
    const key = callId ? `call:${callId}` : `note:${++this.serial}`;
    const record = {
      key,
      createdAt: performance.now(),
      state: "awaiting",
      events: [],
      selectId: null,
      callId: null,
      query: "",
      candidates: [],
      tool: null,
      args: null,
      payload: null,
      error: null,
      preloaded: false,
      selectUnit: null,
      offeredUnit: null,
      callUnit: null,
      returnUnit: null,
      injectedUnit: null,
      injectedAt: 0,
      ack: null,
      answer: null,
      answerFrom: 0,
      ...fields,
    };
    record.ack = this.recentUtterance(record.createdAt);
    // The model has moved on: results it got earlier no longer wait for an answer.
    for (const other of this.records.values())
      if (other.injectedUnit !== null && !other.answer) {
        other.answerClosed = true;
        if (other.state === "delivered") {
          other.state = "consumed";
          this.paint(other);
        }
      }
    this.records.set(key, record);
    if (callId) this.byCall.set(callId, record);
    this.pulse();
    return record;
  }
  selected(e) {
    const record = this.create(e.call_id, {
      selectId: e.call_id,
      generation: e.generation,
      query: e.query || "",
      candidates: e.candidates || [],
      selectUnit: e.unit_index,
    });
    record.events.push(e);
    this.log(
      "tool",
      tf("logCallFind", {
        query: clip(record.query, 36),
        names: record.candidates.map((hit) => hit.name).join(", ") || "—",
      }),
    );
    this.changed(record);
  }
  submitted(e) {
    let record = this.searchFor(e.tool);
    if (record) this.byCall.set(e.call_id, record);
    else {
      const via = this.records.get(this.visible.via);
      record = this.create(e.call_id, {
        generation: e.generation,
        preloaded: this.hotNames.has(e.tool),
        // A second call from the same search shares that search's candidates.
        ...(via && via.candidates.some(hit => hit.name === e.tool)
          ? { query: via.query, candidates: via.candidates, selectUnit: via.selectUnit, offeredUnit: via.offeredUnit }
          : {}),
      });
    }
    record.events.push(e);
    Object.assign(record, { callId: e.call_id, tool: e.tool, args: e.arguments || {}, callUnit: e.unit_index, state: "calling" });
    this.used.set(e.tool, (this.used.get(e.tool) || 0) + 1);
    this.usage(new Map(this.used));
    this.log(
      "tool",
      tf("logCallSubmit", { title: toolTitle(e.tool), args: argumentLine(record.args) || "—", unit: unitLabel(e.unit_index) }),
    );
    this.changed(record);
  }
  /** The search whose candidates put this tool in front of the model, if it hasn't been called. */
  searchFor(tool) {
    const via = this.records.get(this.visible.via);
    if (via && !via.callId && via.state === "awaiting" && via.candidates.some(hit => hit.name === tool)) return via;
    return (
      [...this.records.values()]
        .reverse()
        .find((record) => record.selectId && !record.callId && record.state === "awaiting" && record.candidates.some((hit) => hit.name === tool)) ||
      null
    );
  }
  /** Results for a call this page never saw submitted still get a card. */
  recordFor(e) {
    return this.byCall.get(e.call_id) || this.create(e.call_id, { callId: e.call_id, tool: e.tool, generation: e.generation });
  }
  completed(e) {
    const record = this.recordFor(e);
    record.events.push(e);
    Object.assign(record, { payload: e.payload || {}, returnUnit: e.unit_index, state: "returned" });
    const payload = record.payload;
    this.log(
      "tool",
      tf("logCallReturn", {
        title: toolTitle(record.tool),
        headline: headline(record.tool, payload.result, record.args || {}) || "—",
        source: payload.is_fixture ? t("callFixture") : sourceLabel(payload.source),
        ms: payload.elapsed_ms ?? "—",
      }),
    );
    this.changed(record);
  }
  failed(e) {
    const record = this.recordFor(e);
    record.events.push(e);
    Object.assign(record, { error: e.error || {}, returnUnit: e.unit_index, state: "failed" });
    this.log("err", tf("logCallFail", { title: toolTitle(record.tool), error: rejectionText(record.error.code, record.error.message) }));
    this.changed(record);
  }
  injected(e) {
    if (e.action_kind === "expose") {
      this.hotNames = new Set(e.pinned_tool_names || this.offered);
      this.visible = { names: new Set(e.visible_tool_names || this.offered), via: "preloaded" };
      if (this.offered.length) this.log("tool", tf("logCallPreload", { count: this.offered.length, unit: unitLabel(e.delivered_unit) }));
      return;
    }
    // A rejection's error goes back to the model too, so it can correct itself.
    const record =
      e.action_kind === "error"
        ? [...this.records.values()].reverse().find((item) => item.state === "rejected" && item.injectedUnit === null && !item.router)
        : this.byCall.get(e.call_id);
    if (!record) return;
    record.events.push(e);
    if (e.action_kind === "select") {
      record.offeredUnit = e.delivered_unit;
      this.visible = { names: new Set(e.visible_tool_names || [...this.hotNames, ...record.candidates.map(hit => hit.name)]), via: record.key };
      this.log("tool", tf("logCallOffer", { count: record.candidates.length, unit: unitLabel(e.delivered_unit) }));
    } else {
      record.injectedUnit = e.delivered_unit;
      record.injectedAt = performance.now();
      if (record.state === "returned") record.state = "delivered";
      // Still talking when the result lands: the rest of that utterance is the answer.
      if (record.ack && !record.ack.ended && !record.answer) {
        record.answer = record.ack;
        record.answerFrom = record.ack.text.length;
        if (record.state === "delivered") record.state = "answering";
      }
      if (record.tool) this.log("tool", tf("logCallInject", { title: toolTitle(record.tool), unit: unitLabel(e.delivered_unit) }));
    }
    this.changed(record);
  }
  rejected(e) {
    // A repeated call_id is reported twice: once by the runtime, once as the raised error.
    const last = [...this.records.values()].at(-1);
    if (last?.state === "rejected" && last.error?.code === e.code && last.rejectUnit === e.unit_index) {
      last.events.push(e);
      if (!last.error.message && e.message) last.error.message = e.message;
      if (e.call_id) last.callId = e.call_id;
      this.paint(last);
      return;
    }
    const record = this.create(null, {
      state: "rejected",
      router: Boolean(e.router),
      callId: e.call_id || null,
      tool: e.requested_tool || null,
      error: { code: e.code, message: typeof e.message === "string" ? e.message : JSON.stringify(e.message ?? "") },
      rejectUnit: e.unit_index,
    });
    record.events.push(e);
    this.log("err", tf("logCallReject", { reason: this.reason(record) }));
    this.changed(record);
  }
  settle(e, state) {
    const record = this.byCall.get(e.call_id);
    if (!record) return;
    record.events.push(e);
    record.state = state;
    this.log("tool", tf(state === "stale" ? "logCallStale" : "logCallCancel", { title: toolTitle(record.tool) || record.selectId }));
    this.changed(record);
  }
  /** An interruption empties the outbox: unsent search results and returns never reach the model. */
  generationCancelled(e) {
    let count = 0;
    for (const record of this.records.values()) {
      if (record.generation !== e.previous_generation) continue;
      if ((record.state === "awaiting" && record.offeredUnit === null) || record.state === "returned") {
        record.state = "cancelled";
        record.events.push(e);
        count++;
        this.changed(record);
      }
    }
    if (count) this.log("tool", tf("logCallInterrupted", { count }));
  }

  // ----- speech around a call ---------------------------------------------------
  recentUtterance(now) {
    const used = new Set([...this.records.values()].flatMap((record) => [record.answer, record.ack]));
    return [...this.utterances.values()].reverse().find((item) => now - item.startedAt <= ACK_WINDOW_MS && !used.has(item)) || null;
  }
  speech(message) {
    const id = String(message.utterance_id ?? "");
    let utterance = this.utterances.get(id);
    if (!utterance) {
      utterance = { id, text: "", unit: message.tick, startedAt: performance.now(), ended: false, interrupted: false, during: false };
      this.utterances.set(id, utterance);
      this.utteranceStarted(utterance);
    }
    if (!message.text) return;
    utterance.text += message.text;
    for (const record of this.records.values()) if (record.answer === utterance || record.ack === utterance) this.changed(record);
  }
  utteranceStarted(utterance) {
    for (const record of this.records.values()) {
      if (record.answer || record.answerClosed) continue;
      if (record.injectedUnit !== null) {
        const recent =
          finite(utterance.unit) && finite(record.injectedUnit)
            ? utterance.unit - record.injectedUnit <= ANSWER_WINDOW_UNITS
            : utterance.startedAt - record.injectedAt <= ANSWER_WINDOW_UNITS * 1000;
        if (!recent) continue;
        record.answer = utterance;
        if (record.state === "delivered") record.state = "answering";
        this.changed(record);
      } else if (!record.ack && IN_FLIGHT.has(record.state)) {
        // Spoken while the call was still in flight: the duplex part.
        record.ack = utterance;
        utterance.during = true;
        this.changed(record);
      }
    }
  }
  speechEnded(id, interrupted) {
    const utterance = this.utterances.get(String(id ?? ""));
    if (!utterance || utterance.ended) return;
    utterance.ended = true;
    utterance.interrupted = interrupted;
    for (const record of this.records.values()) {
      if (record.answer !== utterance) continue;
      if (record.state === "answering") record.state = "answered";
      const text = answerText(record);
      if (!interrupted && text) this.log("tool", tf("logCallAnswer", { text: clip(text, 48) }));
      this.changed(record);
    }
    for (const record of this.records.values()) if (record.ack === utterance) this.paint(record);
  }

  // ----- rendering --------------------------------------------------------------
  changed(record) {
    this.paint(record);
    this.activity.show(activityView(record, this.reason(record)), record.key, record.state);
    this.counts(this.activeCount, this.records.size);
  }
  renderAll() {
    for (const node of this.nodes.values())
      for (const wrap of node.querySelectorAll("[data-signature]")) delete wrap.dataset.signature;
    if (!this.records.size) {
      const copy = element("p", "empty-copy small", t(this.connected() ? "callsEmpty" : "callsEmptyOff"));
      this.list.replaceChildren(copy);
    } else for (const record of this.records.values()) this.paint(record);
    $("calls-count").textContent = this.records.size ? tf("callsCount", { active: this.activeCount, total: this.records.size }) : "";
    this.counts(this.activeCount, this.records.size);
  }
  reason(record) {
    if (record.state !== "rejected") return record.error ? rejectionText(record.error.code, record.error.message) : "";
    return record.router ? routerErrorText(record.error.message) : rejectionText(record.error.code, record.error.message);
  }
  skeleton(record) {
    const card = element("article", "call-card");
    card.dataset.key = record.key;
    const head = element("header", "call-head");
    const titles = element("div", "call-titles");
    titles.append(element("h4"), element("small"));
    const status = element("span", "call-status");
    status.append(element("i"), element("b"));
    head.append(element("span", "call-glyph"), titles, status);
    card.append(head);
    for (const name of ["query", "args", "result", "speech"]) {
      const wrap = element("div", "reveal");
      wrap.dataset.part = name;
      wrap.append(element("div", "reveal-inner"));
      card.append(wrap);
    }
    const rail = element("ol", "rail call-rail");
    for (const step of STEPS) {
      const item = element("li", "rail-step");
      item.dataset.step = step;
      item.append(element("i"), element("span"), element("small"));
      rail.append(item);
    }
    const more = element("details", "call-more");
    const summary = element("summary");
    summary.append(element("span", "call-more-label"), element("span", "call-more-meta"), icon("chevron"));
    more.append(summary, element("div", "call-more-body"));
    card.append(rail, more);
    return card;
  }
  paint(record) {
    let card = this.nodes.get(record.key);
    if (!card) {
      card = this.skeleton(record);
      this.nodes.set(record.key, card);
      this.list.querySelector(".empty-copy")?.remove();
      this.list.prepend(card);
    }
    const info = record.tool ? this.info(record.tool) : null;
    const state = !this.live && IN_FLIGHT.has(record.state) ? "ended" : record.state;
    card.dataset.state = state;
    card.dataset.tone = tone(state);
    card.dataset.source = record.payload ? (record.payload.is_fixture ? "fixture" : record.payload.source==='local_personal_store'?'local':"live") : "";
    const glyphName = record.state === "rejected" ? "bolt" : record.tool ? domainIcon(info?.domainKey) : "search";
    const glyph = card.querySelector(".call-glyph");
    if (glyph.dataset.icon !== glyphName) {
      glyph.dataset.icon = glyphName;
      glyph.replaceChildren(icon(glyphName));
    }
    card.querySelector("h4").textContent = record.tool
      ? toolTitle(record.tool)
      : t(record.state === "rejected" ? "callRejectedTitle" : IN_FLIGHT.has(state) ? "callSearchTitle" : "callSearchDone");
    card.querySelector(".call-titles small").textContent = record.tool
      ? [record.tool, info?.domain].filter(Boolean).join(" · ")
      : record.state === "rejected"
        ? [record.router ? t("callRouter") : record.error?.code, unitLabel(record.rejectUnit)].filter(Boolean).join(" · ")
        : counted("callCandidates", record.candidates.length);
    card.querySelector(".call-status b").textContent = t("callState_" + state);

    // What the model looked for — only until it picks a tool.
    this.part(card, "query", !record.tool && record.query ? record.query + record.candidates.length : null, (inner) => {
      inner.append(element("p", "call-query", `“${record.query}”`));
      const chips = element("div", "call-chips");
      for (const hit of record.candidates) chips.append(element("span", "call-chip", toolTitle(hit.name)));
      inner.append(chips);
    });
    // What it called with.
    this.part(card, "args", record.tool && record.args ? JSON.stringify(record.args) : null, (inner) => {
      const list = element("dl", "call-args");
      const rows = fieldRows(record.args);
      for (const row of rows) {
        const pair = element("div");
        pair.append(element("dt", null, row.label), element("dd", null, row.value));
        list.append(pair);
      }
      if (!rows.length) list.append(element("div", "call-args-none", t("callNoArgs")));
      inner.append(list);
    });
    // What came back, or why nothing did.
    const outcome = record.payload
      ? JSON.stringify([record.payload.result, record.payload.source, record.payload.elapsed_ms, record.payload.is_fixture, record.payload.provenance])
      : record.error
        ? JSON.stringify(record.error)
        : null;
    this.part(card, "result", outcome, (inner) => {
      const module = record.payload && this.modules ? views.render(record) : null;
      if (module) {
        inner.append(module);
        return;
      }
      const box = element("div", "call-result");
      if (record.payload) {
        const payload = record.payload;
        box.append(element("p", "call-headline", headline(record.tool, payload.result, record.args || {}) || "—"));
        const rows = detailRows(record.tool, payload.result);
        if (rows.length) {
          const list = element("dl", "call-fields");
          for (const row of rows) {
            const pair = element("div");
            pair.append(element("dt", null, row.label), element("dd", null, row.value));
            list.append(pair);
          }
          box.append(list);
        }
        const source = element("p", "call-source");
        source.append(
          element("em", null, payload.source==='local_personal_store'?(getLanguage()==='zh'?'本地数据':'Local data'):t(payload.is_fixture ? "callFixture" : "callLive")),
          element(
            "span",
            null,
            [payload.is_fixture ? t("callFixtureSource") : sourceLabel(payload.source), finite(payload.elapsed_ms) ? `${payload.elapsed_ms} ms` : ""]
              .filter(Boolean)
              .join(" · "),
          ),
        );
        box.append(source);
      } else {
        box.classList.add("error");
        box.append(element("p", "call-headline", this.reason(record)));
        if (record.error?.code) box.append(element("p", "call-code", record.error.code));
      }
      inner.append(box);
    });
    // What it said around the call: the acknowledgement, then the answer.
    const ack = record.ack,
      answer = record.answer;
    this.part(
      card,
      "speech",
      ack || answer ? JSON.stringify([ack?.text, ack?.ended, answer?.text, answer?.ended, answer?.interrupted, outcome]) : null,
      (inner) => {
        const list = element("div", "call-speech");
        // One utterance can hold both when the result landed mid-sentence.
        const same = answer && answer === ack;
        if (ack)
          list.append(
            this.line(
              { ...ack, text: same ? ack.text.slice(0, record.answerFrom) : ack.text, ended: same || ack.ended, interrupted: !same && ack.interrupted },
              ack.during ? "callDuring" : "callBefore",
              [],
            ),
          );
        if (answer) {
          const terms = record.payload ? groundingTerms(record.payload.result) : [];
          list.append(this.line({ ...answer, text: answerText(record), unit: same ? record.injectedUnit : answer.unit }, record.error ? "callAfterError" : "callAfter", terms));
        }
        inner.append(list);
      },
    );

    // A module that shows every argument in context folds the argument chips away;
    // the exact arguments stay in the raw events under 详情.
    card.classList.toggle(
      "tv-covered",
      Boolean(record.payload && views.covers(record.tool) && card.querySelector('.reveal[data-part="result"] .tv')),
    );

    // The rail: one dot per step, the unit it happened in underneath.
    const rail = card.querySelector(".call-rail");
    rail.hidden = record.state === "rejected";
    const steps = this.steps(record, state);
    let done = 0;
    rail.querySelectorAll(".rail-step").forEach((item, index) => {
      const step = steps[index];
      item.className = `rail-step ${step.status}`;
      item.querySelector("span").textContent = t(step.label);
      item.querySelector("small").textContent = step.unit;
      if (step.status === "done" || step.status === "skip") done = index + 1;
    });
    rail.style.setProperty("--progress", String(Math.max(0, done - 1) / (STEPS.length - 1)));

    // Details: candidates, the checks the runtime enforced, provenance, raw events.
    const meta = [
      record.candidates.length ? counted("callCandidates", record.candidates.length) : null,
      counted("callEvents", record.events.length),
    ]
      .filter(Boolean)
      .join(" · ");
    card.querySelector(".call-more-label").textContent = t("callDetails");
    card.querySelector(".call-more-meta").textContent = meta;
    const body = card.querySelector(".call-more-body");
    const signature = `${getLanguage()}|${record.events.length}|${state}|${answer?.text || ""}`;
    if (body.dataset.signature !== signature) {
      body.dataset.signature = signature;
      body.replaceChildren(...this.details(record, info));
    }
    $("calls-count").textContent = tf("callsCount", { active: this.activeCount, total: this.records.size });
  }
  /** Shows or hides one part; rebuilds it only when what it shows has changed. */
  part(card, name, signature, build) {
    const wrap = card.querySelector(`.reveal[data-part="${name}"]`);
    if (signature === null || signature === undefined) {
      wrap.classList.remove("shown");
      return;
    }
    const key = `${getLanguage()}|${signature}`;
    if (wrap.dataset.signature !== key) {
      wrap.dataset.signature = key;
      const inner = wrap.firstElementChild;
      inner.replaceChildren();
      build(inner);
    }
    if (!wrap.classList.contains("shown")) {
      void wrap.offsetHeight;
      wrap.classList.add("shown");
    }
  }
  line(utterance, labelKey, terms) {
    const row = element("figure", "speech-line" + (labelKey === "callAfter" ? " answer" : ""));
    const caption = element("figcaption");
    caption.append(element("b", null, t(labelKey)), element("span", null, unitLabel(utterance.unit)));
    if (utterance.interrupted) caption.append(element("em", null, t("callInterrupted")));
    const quote = element("blockquote");
    if (!utterance.ended) quote.classList.add("speaking");
    const text = utterance.text || "…";
    let last = 0;
    if (terms.length)
      for (const match of text.matchAll(termPattern(terms))) {
        quote.append(text.slice(last, match.index), element("mark", null, match[0]));
        last = match.index + match[0].length;
      }
    quote.append(text.slice(last));
    row.append(caption, quote);
    return row;
  }
  steps(record, state) {
    const ended = !IN_FLIGHT.has(state);
    const steps = [];
    // find
    steps.push(
      record.query
        ? { status: "done", label: "callStep_find", unit: unitLabel(record.selectUnit) }
        : record.tool
          ? { status: "skip", label: record.preloaded ? "callStep_preloaded" : "callStep_direct", unit: "" }
          : { status: "idle", label: "callStep_find", unit: "" },
    );
    // call
    steps.push(
      record.tool
        ? { status: "done", label: "callStep_call", unit: unitLabel(record.callUnit) }
        : { status: ended ? "idle" : "active", label: "callStep_call", unit: "" },
    );
    // return
    steps.push(
      record.payload
        ? { status: "done", label: "callStep_return", unit: unitLabel(record.returnUnit) }
        : record.error || record.state === "stale"
          ? { status: "bad", label: "callStep_return", unit: unitLabel(record.returnUnit) }
          : { status: record.tool && !ended ? "active" : "idle", label: "callStep_return", unit: "" },
    );
    // inject
    steps.push(
      record.injectedUnit !== null
        ? { status: "done", label: "callStep_inject", unit: unitLabel(record.injectedUnit) }
        : { status: (record.payload || record.error) && !ended ? "active" : record.state === "cancelled" && record.payload ? "bad" : "idle", label: "callStep_inject", unit: "" },
    );
    // answer
    const answer = record.answer;
    steps.push(
      answer?.ended
        ? { status: answer.interrupted ? "bad" : "done", label: "callStep_answer", unit: unitLabel(answer.unit) }
        : { status: record.injectedUnit !== null && !ended ? "active" : "idle", label: "callStep_answer", unit: unitLabel(answer?.unit) },
    );
    return steps;
  }
  details(record, info) {
    const sections = [];
    const section = (labelKey, ...children) => {
      const wrap = element("section", "call-section");
      wrap.append(element("h5", null, t(labelKey)), ...children);
      sections.push(wrap);
    };
    // The search, with the model's pick marked.
    if (record.candidates.length) {
      const top = Math.max(...record.candidates.map((hit) => (finite(hit.score) ? hit.score : 0)), 0) || 1;
      const list = element("ol", "call-candidates");
      record.candidates.forEach((hit, index) => {
        const row = element("li", hit.name === record.tool ? "chosen" : "");
        const name = element("span", "name");
        name.append(element("b", null, toolTitle(hit.name)), element("code", null, hit.name));
        const score = element("span", "score");
        const bar = element("i");
        bar.style.setProperty("--w", ((finite(hit.score) ? hit.score : 0) / top).toFixed(3));
        score.append(bar, element("em", null, finite(hit.score) ? hit.score.toFixed(2) : "—"));
        if (hit.name === record.tool) name.append(element("em", "tag", t("callChosen")));
        row.append(element("span", "rank", String(index + 1)), name, score);
        if (hit.matched_terms?.length) row.append(element("small", "terms", hit.matched_terms.join(" · ")));
        list.append(row);
      });
      const children = [element("p", "call-section-query", `“${record.query}”`), list];
      const rank = record.candidates.findIndex((hit) => hit.name === record.tool);
      if (rank > 0)
        children.push(
          element("p", "call-insight", tf("callPickedLower", { rank: rank + 1, top: toolTitle(record.candidates[0].name) })),
        );
      section("callSearch", ...children);
    }
    // Checks the runtime enforces before it emits each event.
    const checks = element("ul", "call-checks");
    const check = (status, text) => {
      const item = element("li", status);
      item.append(icon(status === "ok" ? "check" : "alert"), element("span", null, text));
      checks.append(item);
    };
    if (record.state === "rejected") {
      check("bad", this.reason(record));
      if (record.injectedUnit !== null) check("ok", tf("check_errorDelivered", { unit: unitLabel(record.injectedUnit) }));
    }
    if (record.callId && record.tool && record.events.some(event => event.type === "submitted")) {
      check(
        "ok",
        tf("check_visible", {
          where:
            record.offeredUnit !== null
              ? tf("callWhereSearch", { unit: unitLabel(record.offeredUnit) })
              : t(record.preloaded ? "callWherePreloaded" : "callWhereContext"),
        }),
      );
      const compatibleVersion = record.events.some(event =>
        event.type === "submitted" && event.schema_version_policy === "visible_schema_authoritative");
      check("ok", t(compatibleVersion ? "check_version_compatible" : "check_version"));
      check("ok", t("check_args"));
      if (info?.spec?.requires_session_authorization) check("ok", t("check_auth"));
    }
    if (record.payload) check("ok", t("check_result"));
    if (record.payload?.is_fixture) check("warn", t("check_fixture"));
    if (record.error && record.state !== "rejected") check("bad", this.reason(record));
    if (record.tool && record.injectedUnit !== null && record.state !== "rejected")
      check("ok", tf(record.error ? "check_errorDelivered" : "check_delivered", { unit: unitLabel(record.injectedUnit) }));
    if (record.answer && record.payload) {
      const terms = groundingTerms(record.payload.result);
      const hits = terms.length ? [...new Set(answerText(record).match(termPattern(terms)) || [])] : [];
      if (hits.length) check("ok", tf("check_grounded", { terms: hits.join("、") }));
    }
    if (checks.children.length) section("callChecks", checks);
    // Where the data came from.
    if (record.payload) {
      const payload = record.payload,
        provenance = payload.provenance || {};
      const rows = [[t("callSource"), payload.is_fixture ? t("callFixtureSource") : sourceLabel(payload.source)]];
      if (provenance.resolved_city) rows.push([t("callResolved"), provenance.resolved_city]);
      if (provenance.timezone || provenance.zone) rows.push([t("callZone"), provenance.timezone || provenance.zone]);
      if (provenance.rate_date) rows.push([t("callRateDate"), formatValue("date", provenance.rate_date)]);
      if (provenance.temperature_semantics === "rounded_daily_mean_celsius") rows.push([t("callMeaning"), t("callDailyMean")]);
      if (provenance.not_transaction_quote) rows.push([t("callMeaning"), t("callNotQuote")]);
      if (finite(payload.elapsed_ms)) rows.push([t("callElapsed"), `${payload.elapsed_ms} ms`]);
      const list = element("dl", "call-fields");
      for (const [label, value] of rows) {
        const pair = element("div");
        pair.append(element("dt", null, label), element("dd", null, value));
        list.append(pair);
      }
      const children = [list];
      if (payload.is_fixture) children.push(element("p", "call-notice", t("callFixtureNotice")));
      const links = element("div", "call-links");
      for (const url of [...(provenance.urls || []), provenance.url].filter((value) => typeof value === "string" && /^https:\/\//.test(value))) {
        const link = element("a", null, new URL(url).hostname);
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.append(icon("arrow"));
        links.append(link);
      }
      if (links.children.length) children.push(links);
      section("callProvenance", ...children);
    }
    const raw = element("pre", "call-raw");
    raw.textContent = record.events.map((event) => JSON.stringify(slim(event))).join("\n");
    section("callRaw", raw);
    return sections;
  }
  /** Bring a card into view and flash it once. */
  focus(key) {
    const card = this.nodes.get(key);
    if (!card) return;
    const body = $("agency-body");
    const top = card.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 18;
    body.scrollTo({ top: Math.max(0, top), behavior: reduced.matches ? "auto" : "smooth" });
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
  }
}

/** What the pill says for a record: a lead glyph, a title, one detail and the data source. */
function activityView(record, reason) {
  const title = record.tool ? toolTitle(record.tool) : t(record.state === "rejected" ? "callRejectedTitle" : "laFinding");
  const result = record.payload ? headline(record.tool, record.payload.result, record.args || {}) : "";
  const badge = record.payload ? (record.payload.is_fixture ? "fixture" : "live") : null;
  const args = record.args
    ? fieldRows(record.args)
        .map((row) => row.value)
        .join(" · ")
    : "";
  switch (record.state) {
    // Lingers on in-flight states are fallbacks: any progress replaces the view first.
    case "awaiting":
      return { lead: "search", tone: "progress", title, detail: record.query, linger: 15000 };
    case "calling":
      return { lead: "spin", tone: "progress", title, detail: args, linger: 20000 };
    case "returned":
    case "delivered":
      return { lead: "check", tone: "ok", title, detail: result, badge, linger: 12000 };
    case "answering":
      return { lead: "wave", tone: "ok", title, detail: result, badge };
    case "answered":
      return { lead: "check", tone: "ok", title, detail: result, badge, linger: 4200 };
    case "failed":
      return { lead: "dot", tone: "bad", title, detail: reason, linger: record.answer?.ended ? 4200 : 9000 };
    case "rejected":
      return { lead: "dot", tone: "warn", title, detail: reason, linger: 6000 };
    default:
      return { lead: "dot", tone: "muted", title, detail: t("callState_" + record.state), linger: 2600 };
  }
}

/** The pill at the top of the stage. It morphs between sizes on a spring and crossfades
 * its content with a blur, the way a live activity does; it collapses once a call settles. */
class LiveActivity {
  constructor(node, status, open) {
    Object.assign(this, { node, status });
    this.key = null;
    this.signature = "";
    this.timer = 0;
    this.pending = 0;
    this.shownAt = 0;
    this.dismissed = new Set(); // "key|state" views that already collapsed
    node.onclick = () => {
      if (this.key) open(this.key);
    };
  }
  show(view, key, state) {
    const signature = `${getLanguage()}|${key}|${JSON.stringify(view)}`;
    if (signature === this.signature || this.dismissed.has(`${key}|${state}`)) return;
    clearTimeout(this.pending);
    const wait = this.shownAt + PILL_DWELL_MS - performance.now();
    if (this.node.dataset.tone === "progress" && key === this.key && wait > 0 && !reduced.matches) {
      this.pending = setTimeout(() => this.show(view, key, state), wait);
      return;
    }
    this.shownAt = performance.now();
    this.signature = signature;
    this.key = key;
    this.state = state;
    clearTimeout(this.timer);
    const node = this.node;
    const shown = !node.hidden && node.classList.contains("on");
    const from = node.offsetWidth;
    node.dataset.tone = view.tone;
    const content = this.content(view);
    for (const old of node.querySelectorAll(".la-content")) {
      if (!shown || reduced.matches) old.remove();
      else if (!old.classList.contains("leaving")) {
        old.classList.add("leaving");
        old.addEventListener("animationend", () => old.remove(), { once: true });
      }
    }
    node.append(content);
    node.hidden = false;
    node.setAttribute("aria-label", [view.title, view.detail, t("laOpen")].filter(Boolean).join(" · "));
    this.status.textContent = [view.title, view.detail].filter(Boolean).join(" · ");
    // Measure the new natural width without a transition, then spring there from the old one.
    node.style.transition = "none";
    node.style.width = "";
    const to = node.offsetWidth;
    node.style.width = `${shown ? from : to}px`;
    void node.offsetWidth;
    node.style.transition = "";
    node.style.width = `${to}px`;
    if (!shown) node.classList.add("on");
    if (view.linger)
      this.timer = setTimeout(() => {
        this.dismissed.add(`${key}|${state}`);
        this.hide();
      }, view.linger);
  }
  content(view) {
    const content = element("span", "la-content");
    const lead = element("span", `la-lead ${view.lead}`);
    if (view.lead === "search") lead.append(icon("search"));
    else if (view.lead === "check") lead.append(icon("check"));
    else if (view.lead === "wave") lead.append(element("i"), element("i"), element("i"));
    else lead.append(element("i"));
    const text = element("span", "la-text");
    text.append(element("b", null, view.title));
    if (view.detail) text.append(element("span", null, view.detail));
    content.append(lead, text);
    if (view.badge) content.append(element("em", `la-badge ${view.badge}`, t(view.badge === "live" ? "toolBadgeLive" : "toolBadgeFixture")));
    return content;
  }
  hide(immediate = false) {
    clearTimeout(this.timer);
    clearTimeout(this.pending);
    if (immediate) this.dismissed.clear();
    this.signature = "";
    this.key = null;
    const node = this.node;
    node.classList.remove("on");
    if (immediate || reduced.matches) {
      node.hidden = true;
      node.replaceChildren();
      return;
    }
    setTimeout(() => {
      if (node.classList.contains("on")) return;
      node.hidden = true;
      node.replaceChildren();
    }, 420);
  }
}
