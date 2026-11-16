/** The capability board: what this runtime can actually do, read live from the
 * runtime itself — /api/status (model type, input modes), /api/config (tool
 * discovery and deployment settings) and /api/tools (function tools,
 * served only by the native Duopra runtime). Nothing is shown as connected
 * unless the runtime reports it. */
import { t, tf, getLanguage } from "./i18n.js?v=20261027-duopra-r9-personal";

const $ = (id) => document.getElementById(id);
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
// Registry domains are Chinese; English labels are looked up by key.
const DOMAINS = { 出行: "travel", 生活: "life", 文化: "culture", 个人: "personal", 信息: "reference", 环境: "environment", 设施: "facilities" };

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
/** Text for a key, or null when no translation exists. */
function maybe(key) {
  const value = t(key);
  return value === key ? null : value;
}
/** Upstream product names stay out of the interface. */
export function displayModel(name) {
  return String(name || "")
    .replace(/(^|[-_ ])(realtime[-_ ]?)?venus(?=$|[-_ ])/gi, "$1")
    .replace(/[-_ ]{2,}/g, "-")
    .replace(/^[-_ ]+|[-_ ]+$/g, "");
}
const domainName = (domain) => maybe("domain_" + (DOMAINS[domain] || domain)) || domain;
const toolMode = (tool) => tool.runtime_mode || tool.native_mode || "fixture";

export class Capabilities {
  constructor({ openSettings, openBoard, send, notify, isSessionReady = () => false }) {
    Object.assign(this, { openSettings, openBoard, send, notify, isSessionReady });
    this.editHot = null;
    this.hotInitialized = false;
    this.savedHot = null;
    try {
      const names = JSON.parse(localStorage.getItem("duplex-native-hot-tools-v1") || "null");
      if (Array.isArray(names) && names.every(name => typeof name === "string")) this.savedHot = names;
    } catch {}
    this.personalAuthorized = false;
    this.discoveryMode = null;
    this.metaTools = [];
    this.status = null;
    this.config = null;
    this.tools = [];
    this.toolSource = "none";
    this.hot = new Set();
    this.used = new Map(); // tool name → calls in this session
    this.domain = "all";
    this.query = "";
    this.loadedAt = 0;
    this.pending = null;
    this.strip = $("cap-strip");
    $("cap-refresh").onclick = () => void this.refresh(true);
    $("tool-search").oninput = (event) => {
      this.query = event.target.value.trim().toLowerCase();
      this.renderTools();
    };
    window.addEventListener("duplex-language", () => this.render());
    // Opening the dashboard re-reads the runtime when what we know is stale.
    $("agency-toggle").addEventListener("click", () => void this.refresh());
    this.render();
  }
  get toolsConnected() {
    return this.toolSource === "runtime" && !this.config?.preview_only;
  }
  /** One tool as the board knows it, for the tool-call cards. */
  info(name) {
    const spec = [...this.metaTools, ...this.tools].find((tool) => tool.name === name);
    return spec ? { spec, domain: domainName(spec.domain), domainKey: spec.domain, live: toolMode(spec) === "live" } : null;
  }
  /** Calls made this session, counted by the tool-call cards. */
  setUsage(used) {
    this.used = used;
    this.renderTools();
  }
  /** Status arrives from the page's own polling; config and tools are fetched here. */
  setStatus(status) {
    const changed = JSON.stringify(status) !== JSON.stringify(this.status);
    this.status = status;
    if (!this.loadedAt) void this.refresh();
    else if (changed) this.render();
  }
  async refresh(force = false) {
    if (this.pending) return this.pending;
    if (!force && this.loadedAt && Date.now() - this.loadedAt < 15000) return;
    const get = async (url) => {
      try {
        const response = await fetch(url, { cache: "no-store" });
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    };
    this.pending = (async () => {
      $("cap-refresh").classList.add("spinning");
      const [status, config] = await Promise.all([get("/api/status"), get("/api/config")]);
      const tools = await get("/api/tools");
      this.status = status;
      this.config = config;
      this.tools = tools?.tools || [];
      this.toolSource = tools ? "runtime" : "none";
      this.metaTools = tools?.meta_tools || [];
      if (this.toolsConnected) this.initializeHotTools();
      this.loadedAt = Date.now();
      this.render();
    })().finally(() => {
      this.pending = null;
      setTimeout(() => $("cap-refresh").classList.remove("spinning"), 400);
    });
    return this.pending;
  }
  /** Native runtimes announce which tools the model can see right now. */
  handle(message) {
    const value =
      message.type === "ready"
        ? message.tool_state
        : message.type === "tool_state"
          ? message.state || message.tool_state || message
          : null;
    if (!value || !("hot_tools" in value || "visible_tools" in value)) return;
    if (value.discovery_mode) this.discoveryMode=value.discovery_mode;
    if (Array.isArray(value.meta_tools)) this.metaTools=value.meta_tools;
    if ("personal_authorized" in value) this.personalAuthorized = Boolean(value.personal_authorized);
    if ("hot_tools" in value || message.type === "ready") {
      this.hot = new Set((value.hot_tools || value.visible_tools || []).map(tool => typeof tool === "string" ? tool : tool.name));
      if (this.editHot === null) this.editHot = new Set(this.hot);
    }
    this.renderTools();
  }

  initializeHotTools() {
    const allowed = new Set(this.tools.map(tool => tool.name));
    const source = this.hotInitialized
      ? [...(this.editHot || this.hot)]
      : this.savedHot ?? this.config?.default_hot_tools ?? [];
    const names = [...new Set(source)].filter(name => allowed.has(name)).slice(0, 8);
    this.editHot = new Set(names);
    if (!this.isSessionReady()) this.hot = new Set(names);
    this.hotInitialized = true;
  }
  rememberHotTools() {
    this.savedHot = [...(this.editHot || this.hot)];
    try { localStorage.setItem("duplex-native-hot-tools-v1", JSON.stringify(this.savedHot)); } catch {}
  }
  sessionTools() {
    if (!this.config?.local_tools) return null;
    if (!this.toolsConnected || !this.hotInitialized) throw Error(t("nativeHotNotLoaded"));
    this.rememberHotTools();
    return [...this.editHot]; // An explicit [] must survive the handshake.
  }
  applyHotTools() {
    this.rememberHotTools();
    if (this.isSessionReady()) {
      this.send({ type: "tools_config", names: [...this.editHot] });
    } else {
      this.hot = new Set(this.editHot);
      this.notify(t("nativeHotSaved"));
      this.renderTools();
    }
  }

  // ----- the model of what is connected -------------------------------------
  items() {
    const status = this.status;
    const online = status?.configured === true;
    const list = [
      { id: "duplex", group: "interact", icon: "wave", status: online ? "on" : "na", chip: true },
    ];
    if (this.toolsConnected)
      list.push(
        { id: "toolcall", group: "act", icon: "bolt", status: "on", chip: true },
        { id: "select", group: "act", icon: "search", status: "on" },
      );
    return list;
  }
  runtimeLine() {
    const status = this.status;
    if (!status) return t("capOfflineShort");
    const model = displayModel(status.model) || "Duopra";
    const modes = t("capModes_audio");
    if (this.toolsConnected) return tf("capRuntimeNative", { model, modes, count: this.tools.length });
    return `${model} · ${modes}`;
  }

  // ----- rendering --------------------------------------------------------------
  render() {
    const items = this.items();
    this.renderStrip(items);
    this.renderBoard(items);
    this.renderTools();
    window.dispatchEvent(new CustomEvent("duplex-capabilities"));
  }
  renderStrip(items) {
    // Nothing is shown until the runtime has answered, so chips never flash a wrong state.
    if (!this.loadedAt) return;
    const chips = [];
    for (const item of items.filter((entry) => entry.chip)) {
      const chip = element("button", `cap-chip ${item.status}`);
      chip.type = "button";
      chip.append(element("i"), element("span", null, t(`cap_${item.id}_short`)));
      chip.title = `${t(`cap_${item.id}_title`)} · ${t("capStatus_" + item.status)}`;
      chip.onclick = () => this.openBoard(item.id);
      chips.push(chip);
    }
    if (this.tools.length) {
      const connected = this.toolsConnected;
      const chip = element("button", `cap-chip ${connected ? "on" : "na"}`);
      chip.type = "button";
      chip.append(
        element("i"),
        element("span", null, connected ? tf("capChipTools", { count: this.tools.length }) : t("capChipToolsOff")),
      );
      chip.onclick = () => this.openBoard("tools");
      chips.push(chip);
    }
    const all = element("button", "cap-chip more");
    all.type = "button";
    all.append(element("span", null, t("capAll")), icon("arrow"));
    all.onclick = () => this.openBoard(null);
    chips.push(all);
    chips.forEach((chip, index) => chip.style.setProperty("--i", index));
    this.strip.replaceChildren(...chips);
    // Chips arrive with a stagger once; later updates swap in place.
    if (this.loadedAt) requestAnimationFrame(() => setTimeout(() => this.strip.classList.add("settled"), 900));
  }
  renderBoard(items) {
    const connected = items.filter((item) => item.status === "on").length;
    $("cap-connected").textContent = String(connected);
    $("cap-total").textContent = String(items.length);
    $("cap-runtime-line").textContent = this.runtimeLine();
    $("cap-updated").textContent = this.loadedAt
      ? tf("capUpdated", {
          time: new Date(this.loadedAt).toLocaleTimeString(getLanguage(), {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          }),
        })
      : "";
    $("cap-offline").hidden = Boolean(this.status) || !this.loadedAt;
    const groups = [];
    for (const group of ["interact", "act"]) {
      const members = items.filter((item) => item.group === group);
      if (!members.length) continue;
      const section = element("section", "cap-group");
      const head = element("div", "section-head");
      head.append(
        element("h3", null, t("capGroup_" + group)),
        element("span", "section-count", `${members.filter((item) => item.status === "on").length} / ${members.length}`),
      );
      section.append(head, ...members.map((item) => this.card(item)));
      groups.push(section);
    }
    $("cap-groups").replaceChildren(...groups);
  }
  card(item) {
    const card = element("article", `cap-card ${item.status}`);
    card.id = "cap-" + item.id;
    const head = element("header");
    const pill = element("span", "cap-status");
    pill.append(element("i"), document.createTextNode(t("capStatus_" + item.status)));
    head.append(icon(item.icon), element("h4", null, t(`cap_${item.id}_title`)), pill);
    card.append(head, element("p", "cap-desc", t(`cap_${item.id}_desc`)));
    if (item.note) card.append(element("p", "cap-note", t(item.note)));
    const example = maybe(`cap_${item.id}_try`);
    if (item.status === "on" && example) {
      const line = element("p", "cap-try");
      line.append(element("span", null, t("capTry")), document.createTextNode(example));
      card.append(line);
    }
    if (item.facts?.length) {
      const facts = element("dl", "cap-facts");
      for (const [key, value] of item.facts) facts.append(element("dt", null, t(key)), element("dd", null, value));
      card.append(facts);
    }
    if (item.settings && (item.status === "setup" || item.status === "off")) {
      const button = element("button", "text-button cap-action", t(item.status === "setup" ? "capSetup" : "capEnable"));
      button.type = "button";
      button.onclick = () => this.openSettings();
      card.append(button);
    }
    return card;
  }
  renderTools() {
    const section = $("cap-tools");
    section.hidden = !this.tools.length;
    if (!this.tools.length) return;
    const opened=new Set([...$("tool-list").querySelectorAll('details[open]')].map(n=>n.dataset.toolName));
    const connected = this.toolsConnected;
    this.renderNativeControls(connected);
    this.renderMetaTools(connected);
    const count = this.tools.length,
      live = this.tools.filter((tool) => toolMode(tool) === "live").length;
    $("tools-count").textContent = connected ? tf("toolsCountConnected", { count, live }) : t("toolsCountNot");
    $("tools-banner").textContent = tf(connected ? "toolsBannerNative" : "toolsBannerHarness", { count, live });
    if (connected) {
      const local=this.tools.filter(tool=>toolMode(tool)==="local").length;
      const fixture=this.tools.filter(tool=>toolMode(tool)==="fixture").length;
      const custom=this.tools.filter(tool=>toolMode(tool)==="custom").length;
      $("tools-banner").textContent=getLanguage()==='zh'
        ? `${live} 项真实数据/计算 · ${local} 项本地日程/待办${custom?` · ${custom} 项自定义工具`:''} · ${fixture} 项演示数据（固定结果，不代表真实查询）。工具选择：本地 Duopra；执行：服务器工具程序。`
        : `${live} real data/computation · ${local} local calendar/tasks${custom?` · ${custom} custom tools`:''} · ${fixture} demos with fixed results. Local Duopra selects tools; server adapters execute them.`;
    }
    $("tools-banner").classList.toggle("off", !connected);
    const counts = new Map();
    for (const tool of this.tools) counts.set(tool.domain, (counts.get(tool.domain) || 0) + 1);
    const chips = [["all", count], ...counts.entries()].map(([key, total]) => {
      const chip = element("button", "domain-chip" + (this.domain === key ? " selected" : ""));
      chip.type = "button";
      chip.append(element("span", null, key === "all" ? t("toolsAll") : domainName(key)), element("b", null, String(total)));
      chip.onclick = () => {
        this.domain = key;
        this.renderTools();
      };
      return chip;
    });
    $("tool-domains").replaceChildren(...chips);
    $("tool-search").placeholder = t("toolsSearch");
    const query = this.query;
    const shown = this.tools
      .filter((tool) => this.domain === "all" || tool.domain === this.domain)
      .filter(
        (tool) =>
          !query ||
          `${tool.name} ${tool.description || ""} ${tool.domain} ${domainName(tool.domain)}`.toLowerCase().includes(query),
      )
      .map((tool, index) => [tool, index])
      // Tools called this session first, then those the model can see, then real data sources.
      .sort(
        ([a, ai], [b, bi]) =>
          (this.used.get(b.name) || 0) - (this.used.get(a.name) || 0) ||
          this.hot.has(b.name) - this.hot.has(a.name) ||
          (toolMode(b) === "live") - (toolMode(a) === "live") ||
          ai - bi,
      )
      .map(([tool]) => {const row=this.toolRow(tool,connected);row.dataset.toolName=tool.name;row.open=opened.has(tool.name);return row;});
    $("tool-list").replaceChildren(...(shown.length ? shown : [element("p", "empty-copy small", t("toolsEmpty"))]));
  }
  renderNativeControls(connected) {
    let root=$("native-tool-controls");
    if(!root){root=element("div","native-tool-controls");root.id="native-tool-controls";$("tools-banner").after(root);}
    root.hidden=!connected;if(!connected)return;root.replaceChildren();
    const selected=this.editHot||this.hot;
    const update=element("button",null,tf(this.isSessionReady()?"nativeHotApply":"nativeHotSave",{count:selected.size}));update.type="button";
    update.onclick=()=>this.applyHotTools();
    const auth=element("label","native-auth"),check=document.createElement("input");check.type="checkbox";check.checked=this.personalAuthorized;
    check.onchange=()=>{if(this.isSessionReady()&&!this.send({type:"tools_authorize",allowed:check.checked})){check.checked=this.personalAuthorized;return;}this.personalAuthorized=check.checked;window.dispatchEvent(new CustomEvent('duplex-personal-permission'));};auth.append(check,document.createTextNode(t("nativePersonalAuth")));
    const search=document.createElement("input");search.type="search";search.placeholder=t("nativeSearchHint");search.setAttribute("aria-label",t("nativeSearchHint"));
    const discover=element("button",null,t("nativeSearch"));discover.type="button";
    discover.onclick=()=>{const query=search.value.trim();if(query)this.send({type:"tools_discover",query,top_k:3});};
    const note=element("small",null,t("nativeSearchNote"));
    const hotHint=element("small", "native-hot-hint", t(this.isSessionReady()?"nativeHotActiveHint":"nativeHotBeforeHint"));
    root.append(update,hotHint,auth,search,discover,note);
    const mode=this.discoveryMode??this.config?.deployment_application?.tool_discovery?.mode;
    if(mode==='function')root.append(element('small',null,t('nativeDiscoveryFn')));
  }
  renderMetaTools(connected) {
    let root = $("native-meta-tools");
    if (!root) {
      root = element("section", "native-meta-tools");
      root.id = "native-meta-tools";
      $("native-tool-controls").after(root);
    }
    const mode = this.isSessionReady() ? this.discoveryMode : this.config?.deployment_application?.tool_discovery?.mode;
    const tools = connected && mode === "function" ? this.metaTools : [];
    root.hidden = !tools.length;
    const opened = new Set([...root.querySelectorAll('details[open]')].map(row => row.dataset.toolName));
    root.replaceChildren();
    if (!tools.length) return;
    root.append(element("p", "native-meta-heading", t("nativeFixedTools")));
    for (const tool of tools) {
      const row = this.toolRow({ ...tool, example_arguments: { query: "汇率 / currency exchange", top_k: 3 } }, true);
      row.dataset.toolName = tool.name;
      row.open = opened.has(tool.name);
      root.append(row);
    }
  }
  toolRow(tool, connected) {
    const fixed = tool.runtime_role === "meta";
    const local = toolMode(tool) === "local";
    const computed = tool.name === "timezone.offset";
    const live = toolMode(tool) === "live";
    const custom = toolMode(tool) === "custom";
    const item = element("details", "tool-item" + (connected ? "" : " off"));
    const summary = element("summary");
    const text = element("span", "tool-text");
    text.append(element("b", null, tool.name), element("small", null, fixed ? t("nativeDiscoveryDescription") : tool.description || ""));
    const badges = element("span", "tool-badges");
    if (this.used.get(tool.name)) badges.append(element("em", "used", tf("toolBadgeUsed", { count: this.used.get(tool.name) })));
    if (this.hot.has(tool.name)) badges.append(element("em", "hot", t("toolBadgeHot")));
    badges.append(element("em", fixed ? "fixed" : local ? "local" : live || custom ? "live" : "fixture", computed ? (getLanguage()==='zh'?'本地计算':'Local computation') : local ? (document.documentElement.lang==='zh'?'本地':'Local') : t(fixed ? "nativeFixedBadge" : custom ? "toolBadgeCustom" : live ? "toolBadgeLive" : "toolBadgeFixture")));
    if (tool.requires_session_authorization) badges.append(element("em", "auth", t("toolBadgeAuth")));
    if(connected && !fixed){
      const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.className="native-hot-checkbox";
      checkbox.checked=(this.editHot||this.hot).has(tool.name);checkbox.setAttribute("aria-label",t("nativeHotToggle")+" "+tool.name);
      checkbox.onclick=event=>event.stopPropagation();checkbox.onchange=()=>{
        if(!this.editHot)this.editHot=new Set(this.hot);
        if(checkbox.checked&&this.editHot.size>=8){checkbox.checked=false;this.notify(t("nativeHotLimit"),true);return;}
        checkbox.checked?this.editHot.add(tool.name):this.editHot.delete(tool.name);
        this.rememberHotTools();
        if(!this.isSessionReady())this.hot=new Set(this.editHot);
        this.renderTools();
      };summary.append(checkbox);
    }
    summary.append(text, badges);
    const detail = element("div", "tool-detail");
    const block = (labelKey, ...children) => {
      const wrap = element("div");
      wrap.append(element("span", "label", t(labelKey)), ...children);
      return wrap;
    };
    if (fixed) detail.append(element("p", "native-fixed-note", t("nativeDiscoveryFn")));
    if (tool.excluded) detail.append(block("toolExcluded", element("p", null, fixed ? t("nativeDiscoveryExcludes") : tool.excluded)));
    const fields = (schema, required = new Set()) =>
      Object.keys(schema?.properties || {}).map((name) => element("code", null, required.has(name) ? `${name}*` : name));
    const params = fields(tool.parameters, new Set(tool.parameters?.required || []));
    if (params.length) detail.append(block("toolParams", ...params));
    const returns = fields(tool.returns);
    if (returns.length) detail.append(block("toolReturns", ...returns));
    const args = tool.example?.arguments || tool.example_arguments;
    if (args) {
      const sample = element("pre", "tool-example");
      sample.textContent =
        `${tool.name}(${JSON.stringify(args)})` + (tool.example?.result ? `\n→ ${JSON.stringify(tool.example.result)}` : "");
      detail.append(block("toolExample", sample));
    }
    detail.append(
      element(
        "p",
        "tool-meta",
        [
          tool.read_only !== false ? t("toolReadOnly") : null,
          computed ? (getLanguage()==='zh'?'本地 IANA 时区数据库计算':'Computed from the local IANA time-zone database') : local ? (document.documentElement.lang==='zh'?'本地持久化 · 非外部账号':'Local storage · no external account') : connected ? t(fixed ? "nativeDiscoverySource" : live ? "toolSourceLive" : "toolSourceFixture") : t("toolNotConnected"),
        ]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    item.append(summary, detail);
    return item;
  }
  /** Bring one capability (or the tool list) into view and flash it once. */
  focus(id) {
    const body = $("agency-body");
    const target = id === "tools" ? $("cap-tools") : id ? $("cap-" + id) : null;
    if (!target) {
      body.scrollTo({ top: 0, behavior: reduced.matches ? "auto" : "smooth" });
      return;
    }
    body.scrollTo({ top: Math.max(0, target.offsetTop - 18), behavior: reduced.matches ? "auto" : "smooth" });
    target.classList.remove("flash");
    void target.offsetWidth;
    target.classList.add("flash");
  }
}
