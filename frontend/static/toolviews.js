/** Result modules for tool-call cards. Each module re-presents a call's real result in a form
 * people already know — a calendar event, a ticket, a pass, a gauge, a market chart — using
 * only what the runtime returned (result fields, plus UI-only provenance such as chart series),
 * the arguments the model passed, and arithmetic on them (countdowns, conversions, positions
 * on a day). Nothing is added that the payload does not contain, and the data-source line
 * (live / sandbox) always stays at the bottom of the module.
 *
 * Dependencies are injected so the module carries no cache token of its own:
 *   createToolViews({ t, tf, getLanguage, sourceLabel, formatValue })
 * returns { has(tool), covers(tool), render(record), repaint() }; record = { tool, args, payload }. */
export function createToolViews({ t, tf, getLanguage, sourceLabel, formatValue }) {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const zh = () => getLanguage() === "zh";
  const s = (key, vars = {}) => tf("tv_" + key, vars);
  const drawings = new Set(); // live canvases, redrawn when the theme changes

  // ----- helpers -----------------------------------------------------------------------
  function el(tag, className, text) {
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
  const pad = (n) => String(n).padStart(2, "0");
  const locale = () => (zh() ? "zh-CN" : "en");
  function day(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
    return m ? { y: +m[1], m: +m[2], d: +m[3], stamp: Date.UTC(+m[1], +m[2] - 1, +m[3]) } : null;
  }
  function today() {
    const now = new Date();
    return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  }
  /** Whole days from the viewer's today to a date (or the date part of a date-time). */
  const daysUntil = (iso) => (day(iso) ? Math.round((day(iso).stamp - today()) / 864e5) : null);
  function relative(n) {
    if (n === null || n === undefined) return "";
    if (n === 0) return s("today");
    if (n === 1) return s("tomorrow");
    if (n === -1) return s("yesterday");
    return n > 0 ? s("inDays", { n }) : s("daysAgo", { n: -n });
  }
  function dateText(iso, { weekday = true, year = false } = {}) {
    const p = day(iso);
    if (!p) return iso == null ? "—" : String(iso);
    return new Intl.DateTimeFormat(locale(), {
      month: zh() ? "long" : "short",
      day: "numeric",
      ...(weekday ? { weekday: "short" } : {}),
      ...(year || p.y !== new Date().getFullYear() ? { year: "numeric" } : {}),
      timeZone: "UTC",
    }).format(new Date(p.stamp));
  }
  /** "14:00", or the wall-clock part of an ISO date-time as written (no zone conversion). */
  function clock(text) {
    const m = /(?:^|T|\s)(\d{1,2}):(\d{2})/.exec(text || "");
    return m ? { minutes: +m[1] * 60 + +m[2], text: `${pad(+m[1])}:${m[2]}` } : null;
  }
  /** A date-time as written: "11月12日周四 08:00". */
  const when = (iso) => [dateText(iso), clock(iso)?.text].filter(Boolean).join(" ");
  function span(minutes) {
    const h = Math.floor(minutes / 60),
      m = Math.round(minutes % 60);
    return h && m ? s("hoursMinutes", { h, m }) : h ? s("hours", { h }) : s("minutes", { m });
  }
  function nowMinutes() {
    const now = new Date();
    return now.getHours() * 60 + now.getMinutes();
  }
  function money(amount, currency) {
    try {
      return new Intl.NumberFormat(locale(), {
        style: "currency",
        currency,
        minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
        maximumFractionDigits: 2,
      }).format(amount);
    } catch {
      return `${amount} ${currency || ""}`.trim();
    }
  }
  const number = (value, digits = 2) =>
    finite(value) ? new Intl.NumberFormat(locale(), { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value) : "—";
  const signed = (value, digits = 2) => (finite(value) ? `${value > 0 ? "+" : value < 0 ? "−" : ""}${number(Math.abs(value), digits)}` : "—");
  const trend = (value) => (finite(value) ? (value > 0 ? "up" : value < 0 ? "down" : "flat") : "flat");
  function mood(status) {
    const value = String(status || "").toLowerCase();
    if (/(approved|paid|complete|delivered|on_time|open|ok|normal)/.test(value)) return "ok";
    if (/(reject|cancel|fail|denied|closed|suspend)/.test(value)) return "bad";
    if (/(pending|review|submitted|transit|delayed|processing|caution|maintenance)/.test(value)) return "warn";
    return "neutral";
  }
  function palette() {
    const style = getComputedStyle(document.documentElement);
    const v = (name) => style.getPropertyValue(name).trim();
    return {
      ink: v("--ink"), dim: v("--ink-dim"), muted: v("--muted"), faint: v("--faint"), line: v("--line"),
      strong: v("--line-strong"), accent: v("--accent"), fault: v("--fault"), warn: v("--warn"),
      you: v("--you-color"), agent: v("--agent-color"), bg: v("--bg"), up: v("--tv-up"), down: v("--tv-down"),
      ui: v("--font-ui") || "system-ui", mono: v("--font-mono") || "ui-monospace",
    };
  }
  /** Critically damped spring progress (Apple's default feel: no overshoot). */
  const spring = (p) => 1 - (1 + 7 * p) * Math.exp(-7 * p);
  /** A crisp canvas that draws at its laid-out width, redraws on resize and theme change, and
   * plays its first appearance on a spring unless reduced motion is on or the tab is hidden.
   * paint(ctx, width, height, progress, palette). */
  function drawing(height, paint, { duration = 900, className = "" } = {}) {
    const node = el("canvas", `tv-canvas ${className}`.trim());
    node.style.height = `${height}px`;
    node.setAttribute("aria-hidden", "true");
    let width = 0,
      frame = 0,
      fallback = 0,
      played = false;
    const render = (progress) => {
      const w = node.clientWidth;
      if (!w) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (node.width !== Math.round(w * dpr)) {
        node.width = Math.round(w * dpr);
        node.height = Math.round(height * dpr);
      }
      const ctx = node.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, height);
      paint(ctx, w, height, progress, palette());
    };
    const entry = { node, render };
    drawings.add(entry);
    const observer = new ResizeObserver(() => {
      if (!node.isConnected) {
        drawings.delete(entry);
        return observer.disconnect();
      }
      if (node.clientWidth === width) return;
      width = node.clientWidth;
      cancelAnimationFrame(frame);
      clearTimeout(fallback);
      if (played || reduced.matches || document.hidden) {
        played = true;
        return render(1);
      }
      played = true;
      let start = null;
      const step = (time) => {
        start ??= time;
        const p = Math.min(1, (time - start) / duration);
        render(spring(p));
        if (p < 1) frame = requestAnimationFrame(step);
      };
      frame = requestAnimationFrame(step);
      // Frames stop in a background tab; the final drawing must still land.
      fallback = setTimeout(() => {
        cancelAnimationFrame(frame);
        render(1);
      }, duration + 250);
    });
    observer.observe(node);
    return node;
  }
  function roundRect(ctx, x, y, w, h, r) {
    const radius = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.arcTo(x + w, y, x + w, y + h, radius);
    ctx.arcTo(x + w, y + h, x, y + h, radius);
    ctx.arcTo(x, y + h, x, y, radius);
    ctx.arcTo(x, y, x + w, y, radius);
    ctx.closePath();
  }
  function shell(kind, payload, { kicker, tone } = {}) {
    const root = el("div", `tv tv-${kind}`);
    if (tone) root.dataset.tone = tone;
    if (kicker) root.append(el("p", "tv-kicker", kicker));
    const body = el("div", "tv-body");
    root.append(body);
    // The source line is the card's honesty marker; every module keeps it.
    const source = el("p", "call-source tv-source");
    source.append(
      el("em", null, payload.source==='local_personal_store'?(zh()?'本地数据':'Local data'):t(payload.is_fixture ? "callFixture" : "callLive")),
      el(
        "span",
        null,
        [payload.is_fixture ? t("callFixtureSource") : sourceLabel(payload.source), finite(payload.elapsed_ms) ? `${payload.elapsed_ms} ms` : ""]
          .filter(Boolean)
          .join(" · "),
      ),
    );
    root.append(source);
    return { root, body };
  }
  const kick = (...parts) => parts.filter((part) => part !== undefined && part !== null && part !== "").join(" · ");
  function chip(text, className = "") {
    return el("span", `tv-chip ${className}`.trim(), text);
  }
  function capsule(kind, label, glyph) {
    const node = el("span", `tv-status ${kind}`);
    node.append(icon(glyph || (kind === "ok" ? "check" : kind === "bad" ? "close" : "history")), el("span", null, label));
    return node;
  }
  function figure(value, unit, extra) {
    const node = el("p", "tv-figure");
    node.append(el("b", null, value));
    if (unit) node.append(el("span", null, unit));
    if (extra) node.append(extra);
    return node;
  }
  function dots(n, shape = "square") {
    const row = el("div", `tv-dots ${shape}`);
    for (let i = 0; i < n; i++) {
      const dot = el("i");
      dot.style.setProperty("--i", String(i));
      row.append(dot);
    }
    return row;
  }
  function pair(label, value) {
    const node = el("div", "tv-pair");
    node.append(el("span", null, label), el("b", null, value));
    return node;
  }
  /** A day axis with hour ticks; pins mark one time, spans mark a slot. */
  function dayStrip({ pins = [], spans = [], now = false }) {
    const minutes = [...pins.map((pin) => pin.minutes), ...spans.flatMap((item) => [item.from, item.to])];
    const from = Math.max(0, Math.min(6 * 60, ...minutes.map((m) => Math.floor(m / 60 - 1) * 60)));
    const to = Math.min(24 * 60, Math.max(22 * 60, ...minutes.map((m) => Math.ceil(m / 60 + 1) * 60)));
    const current = nowMinutes();
    return drawing(58, (ctx, w, h, p, c) => {
      const left = 6,
        right = w - 6,
        y = 30;
      const x = (m) => left + ((m - from) / (to - from)) * (right - left);
      ctx.strokeStyle = c.strong;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
      ctx.font = `400 9px ${c.mono}`;
      ctx.textAlign = "center";
      ctx.fillStyle = c.faint;
      for (let m = Math.ceil(from / 120) * 120; m <= to; m += 120) {
        ctx.fillRect(x(m) - 0.5, y - 3, 1, 6);
        ctx.fillText(pad(m / 60), x(m), y + 18);
      }
      for (const item of spans) {
        const a = x(item.from),
          b = a + (x(item.to) - a) * p;
        ctx.fillStyle = c[item.color || "accent"];
        ctx.globalAlpha = 0.85;
        roundRect(ctx, a, y - 7, Math.max(3, b - a), 14, 7);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      for (const pin of pins) {
        const px = x(pin.minutes),
          drop = (1 - p) * -10;
        ctx.strokeStyle = c[pin.color || "accent"];
        ctx.fillStyle = c[pin.color || "accent"];
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(px, y + drop - 12);
        ctx.lineTo(px, y + drop);
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(px, y + drop - 14, 4, 0, Math.PI * 2);
        ctx.fill();
      }
      if (now && current >= from && current <= to) {
        const nx = x(current);
        ctx.strokeStyle = c.fault;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(nx, y - 10);
        ctx.lineTo(nx, y + 10);
        ctx.stroke();
        ctx.fillStyle = c.fault;
        ctx.font = `500 9px ${c.ui}`;
        ctx.fillText(s("now"), nx, y - 14);
      }
    });
  }
  /** A value on a coloured scale (Apple Weather style): stops are [position 0..1, colour key]. */
  function scale(value, min, max, stops, { left: lo, right: hi, marks = [] } = {}) {
    return drawing(30, (ctx, w, h, p, c) => {
      const left = 4,
        right = w - 4,
        y = 10;
      const gradient = ctx.createLinearGradient(left, 0, right, 0);
      for (const [at, colour] of stops) gradient.addColorStop(at, c[colour] || colour);
      ctx.fillStyle = gradient;
      ctx.globalAlpha = 0.9;
      roundRect(ctx, left, y - 2.5, right - left, 5, 2.5);
      ctx.fill();
      ctx.globalAlpha = 1;
      const ratio = (Math.min(max, Math.max(min, value)) - min) / (max - min);
      const at = left + ratio * (right - left) * p;
      ctx.fillStyle = c.bg;
      ctx.strokeStyle = c.ink;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(at, y, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.font = `400 8.5px ${c.mono}`;
      ctx.fillStyle = c.faint;
      ctx.textAlign = "left";
      if (lo) ctx.fillText(lo, left, y + 16);
      ctx.textAlign = "right";
      if (hi) ctx.fillText(hi, right, y + 16);
      ctx.textAlign = "center";
      for (const [mark, label] of marks) {
        const mx = left + ((mark - min) / (max - min)) * (right - left);
        ctx.fillRect(mx - 0.5, y + 4, 1, 3);
        ctx.fillText(label, mx, y + 16);
      }
    });
  }
  /** A price line against a dashed baseline: Apple Stocks style. points = [[label, value], ...]. */
  function priceChart(points, { baseline, slots, height = 120, labels = true } = {}) {
    const values = points.map((point) => point[1]).filter(finite);
    if (values.length < 2) return null;
    const last = values.at(-1);
    const direction = finite(baseline) ? trend(last - baseline) : trend(last - values[0]);
    return drawing(
      height,
      (ctx, w, h, p, c) => {
        const top = 8,
          bottom = h - (labels ? 18 : 6),
          left = 2,
          right = w - (labels ? 44 : 2);
        const all = finite(baseline) ? [...values, baseline] : values;
        let hi = Math.max(...all),
          lo = Math.min(...all);
        const margin = (hi - lo || Math.abs(hi) * 0.01 || 1) * 0.08;
        hi += margin;
        lo -= margin;
        const count = Math.max(slots || points.length, points.length);
        const x = (i) => left + (i / Math.max(1, count - 1)) * (right - left);
        const y = (v) => bottom - ((v - lo) / (hi - lo)) * (bottom - top);
        const colour = direction === "down" ? c.down : direction === "up" ? c.up : c.dim;
        if (finite(baseline)) {
          ctx.setLineDash([2, 3]);
          ctx.strokeStyle = c.strong;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(left, y(baseline));
          ctx.lineTo(right, y(baseline));
          ctx.stroke();
          ctx.setLineDash([]);
          if (labels) {
            ctx.font = `400 9px ${c.mono}`;
            ctx.fillStyle = c.faint;
            ctx.textAlign = "left";
            ctx.fillText(number(baseline, Math.abs(baseline) < 10 ? 4 : 2), right + 6, y(baseline) + 3);
          }
        }
        const shown = Math.max(1, Math.round((points.length - 1) * p));
        ctx.beginPath();
        points.slice(0, shown + 1).forEach(([, v], i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
        ctx.strokeStyle = colour;
        ctx.lineWidth = 1.6;
        ctx.lineJoin = "round";
        ctx.stroke();
        const fill = ctx.createLinearGradient(0, top, 0, bottom);
        fill.addColorStop(0, colour);
        fill.addColorStop(1, "transparent");
        ctx.lineTo(x(shown), bottom);
        ctx.lineTo(x(0), bottom);
        ctx.closePath();
        ctx.globalAlpha = 0.16;
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.globalAlpha = 1;
        const [, end] = points[shown];
        ctx.fillStyle = colour;
        ctx.beginPath();
        ctx.arc(x(shown), y(end), 3, 0, Math.PI * 2);
        ctx.fill();
        if (labels) {
          ctx.font = `400 9px ${c.mono}`;
          ctx.fillStyle = c.faint;
          ctx.textAlign = "left";
          ctx.fillText(points[0][0], left, h - 4);
          if (points.length > 2) {
            ctx.textAlign = "right";
            ctx.fillText(points.at(-1)[0], x(points.length - 1), h - 4);
          }
        }
      },
      { duration: 1100 },
    );
  }

  // ----- templates ------------------------------------------------------------------------
  function counter(payload, { kicker, n, unit, shape, note }) {
    const empty = n === 0;
    const { root, body } = shell("counter", payload, { kicker, tone: empty ? "warn" : undefined });
    body.append(figure(finite(n) ? String(n) : "—", unit));
    if (Number.isInteger(n) && n > 0 && n <= 24) body.append(dots(n, shape));
    if (empty) body.append(el("p", "tv-sub", s("noneNow")));
    if (note) body.append(el("p", "tv-note", note));
    return root;
  }
  function hours(payload, { kicker, opens, closes, date }) {
    const a = clock(opens),
      b = clock(closes);
    const { root, body } = shell("hours", payload, { kicker });
    body.append(el("h5", "tv-title tv-numeric", `${a?.text ?? opens ?? "—"} – ${b?.text ?? closes ?? "—"}`));
    if (a && b && b.minutes > a.minutes) {
      // "Open now" only when the asked date is today; hours without a date say nothing about now.
      const isToday = date ? daysUntil(date) === 0 : false;
      const status = isToday ? s(nowMinutes() >= a.minutes && nowMinutes() < b.minutes ? "openNow" : "closedNow") : "";
      body.append(el("p", "tv-sub", kick(s("openFor", { d: span(b.minutes - a.minutes) }), status, date ? relative(daysUntil(date)) : "")));
      body.append(dayStrip({ spans: [{ from: a.minutes, to: b.minutes }], now: isToday }));
    }
    return root;
  }
  function outage(payload, { kicker, start, end, tone = "warn" }) {
    const a = Date.parse(start),
      b = Date.parse(end);
    const { root, body } = shell("outage", payload, { kicker, tone });
    const sameDay = (start || "").slice(0, 10) === (end || "").slice(0, 10);
    const title = sameDay ? `${dateText(start)} ${clock(start)?.text ?? ""}–${clock(end)?.text ?? ""}` : `${when(start)} – ${when(end)}`;
    body.append(el("h5", "tv-title", title));
    if (finite(a) && finite(b)) {
      const now = Date.now();
      const state = now < a ? relative(daysUntil(start)) : now > b ? s("ended") : s("ongoing");
      body.append(el("p", "tv-sub", kick(s("lasts", { d: span((b - a) / 60000) }), state)));
      const ca = clock(start),
        cb = clock(end);
      if (sameDay && ca && cb && cb.minutes > ca.minutes) body.append(dayStrip({ spans: [{ from: ca.minutes, to: cb.minutes, color: tone === "bad" ? "fault" : "warn" }], now: daysUntil(start) === 0 }));
    }
    return root;
  }
  /** Tickets and passes: a headline block, a tear line, then the time. */
  function ticket(payload, { kicker, title, chips = [], date, time, timeLabel, tone }) {
    const { root, body } = shell("ticket", payload, { kicker, tone });
    body.append(el("h5", "tv-title tv-ticket-title", title || "—"));
    const tags = chips.filter(Boolean);
    if (tags.length) {
      const row = el("div", "tv-chips");
      for (const text of tags) row.append(typeof text === "string" ? chip(text) : text);
      body.append(row);
    }
    body.append(el("div", "tv-perforation"));
    const foot = el("div", "tv-row tv-between tv-ticket-foot");
    foot.append(el("span", "tv-sub", kick(dateText(date), relative(daysUntil(date)))));
    if (time) {
      const right = el("span", "tv-time-block");
      if (timeLabel) right.append(el("small", null, timeLabel));
      right.append(el("b", "tv-time", time));
      foot.append(right);
    }
    body.append(foot);
    return root;
  }
  function status(payload, { kicker, ok, label, title, reason, glyph }) {
    const { root, body } = shell("status", payload, { kicker, tone: ok === false ? "bad" : undefined });
    const head = el("div", "tv-row tv-between");
    head.append(el("h5", "tv-title", title || label), capsule(ok === false ? "bad" : ok === null ? "warn" : "ok", label, glyph));
    body.append(head);
    if (reason) body.append(el("p", "tv-sub", reason));
    return root;
  }

  // ----- 个人 · productivity --------------------------------------------------------------
  function calendar({ args, payload }) {
    if(Array.isArray(payload.result?.events))return personalCalendar({args,payload});
    const r = payload.result || {};
    const n = daysUntil(args.date);
    const p = day(args.date);
    const { root, body } = shell("calendar", payload, { kicker: s("calendarKicker", { id: args.calendar_id ?? "—" }) });
    const tile = el("div", "tv-datetile");
    const part = (options) => (p ? new Intl.DateTimeFormat(locale(), { ...options, timeZone: "UTC" }).format(new Date(p.stamp)) : "");
    tile.append(el("span", "tv-datetile-week", part({ weekday: "short" }) || "—"), el("b", null, p ? String(p.d) : "—"), el("span", "tv-datetile-month", part({ month: "short" })));
    const main = el("div", "tv-event");
    const time = clock(r.starts);
    main.append(el("h5", "tv-title", r.title || "—"), el("p", "tv-sub", kick(time ? s("startsAt", { time: time.text }) : r.starts, relative(n))));
    const row = el("div", "tv-row");
    row.append(tile, main);
    body.append(row);
    if (time) body.append(dayStrip({ pins: [{ minutes: time.minutes }], now: n === 0 }));
    return root;
  }
  function personalCalendar({args,payload}) {
    const r=payload.result||{},items=r.events||[r.item].filter(Boolean);
    const {root,body}=shell('calendar',payload,{kicker:zh()?'本地日程 · 北京时间':'Local calendar · Asia/Shanghai'});
    const labels={created:['已创建','Created'],updated:['已修改','Updated'],deleted:['已删除 · 可撤销','Deleted · undo available'],restored:['已恢复','Restored']};
    if(r.state)body.append(capsule(r.state==='deleted'?'warn':'ok',(labels[r.state]||[r.state,r.state])[zh()?0:1]));
    if(!items.length)body.append(el('p','tv-sub',zh()?'当天暂无日程。':'No events on this date.'));
    for(const item of items){const row=el('div','personal-result-row');row.append(el('h5','tv-title',item.title));
      const fmt=v=>v?new Intl.DateTimeFormat(zh()?'zh-CN':'en-GB',{timeZone:'Asia/Shanghai',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v)):'';
      row.append(el('p','tv-sub',`${fmt(item.start)} — ${fmt(item.end)}`));
      if(item.location)row.append(el('p','tv-sub',item.location));body.append(row)}
    const manage=el('button','organizer-secondary',zh()?'管理日程':'Manage calendar');manage.type='button';
    manage.onclick=()=>window.dispatchEvent(new CustomEvent('duplex-personal-open',{detail:{kind:'calendar'}}));body.append(manage);return root;
  }
  function personalTasks({args,payload}) {
    const r=payload.result||{},items=r.items||[r.item].filter(Boolean);
    const {root,body}=shell('calendar',payload,{kicker:zh()?'本地待办':'Local tasks'});
    const states={created:['已添加','Created'],updated:['已更新','Updated'],deleted:['已删除 · 可撤销','Deleted · undo available'],restored:['已恢复','Restored']};
    if(r.state)body.append(capsule(r.state==='deleted'?'warn':'ok',(states[r.state]||[r.state,r.state])[zh()?0:1]));
    if(!items.length)body.append(el('p','tv-sub',zh()?'暂无待办事项。':'No tasks yet.'));
    for(const item of items){const row=el('div','personal-result-row');row.append(el('h5','tv-title',`${item.completed?'✓':'○'} ${item.title}`));
      if(item.due)row.append(el('p','tv-sub',dateText(item.due)));body.append(row)}
    const manage=el('button','organizer-secondary',zh()?'管理待办':'Manage tasks');manage.type='button';
    manage.onclick=()=>window.dispatchEvent(new CustomEvent('duplex-personal-open',{detail:{kind:'task'}}));body.append(manage);return root;
  }
  function room({ args, payload }) {
    const free = payload.result?.available === true;
    const a = clock(args.start),
      b = clock(args.end);
    const length = Date.parse(args.end) - Date.parse(args.start);
    const { root, body } = shell("room", payload, { kicker: s("roomKicker", { date: dateText(args.start) }), tone: free ? "ok" : "bad" });
    const head = el("div", "tv-row tv-between");
    head.append(el("h5", "tv-title tv-mono-title", args.room ?? "—"), capsule(free ? "ok" : "bad", s(free ? "free" : "busy")));
    body.append(head);
    if (a && b) {
      body.append(el("p", "tv-sub", kick(`${a.text}–${b.text}`, finite(length) && length > 0 ? span(length / 60000) : "")));
      if (b.minutes > a.minutes) body.append(dayStrip({ spans: [{ from: a.minutes, to: b.minutes, color: free ? "accent" : "fault" }] }));
    }
    return root;
  }
  function leave({ args, payload }) {
    const n = payload.result?.remaining_days;
    const { root, body } = shell("leave", payload, { kicker: s("leaveKicker", { year: args.year ?? "—" }) });
    body.append(figure(finite(n) ? String(n) : "—", s("days")), el("p", "tv-sub", s("leaveSub")));
    if (Number.isInteger(n) && n > 0 && n <= 30) body.append(dots(n));
    return root;
  }
  function expense({ args, payload }) {
    const value = payload.result?.status;
    const tone = mood(value);
    const { root, body } = shell("receipt", payload, { tone });
    const head = el("div", "tv-row tv-between");
    head.append(el("span", "tv-label", s("claim")), el("code", "tv-code", args.claim_id ?? "—"));
    const stamp = el("div", `tv-stamp ${tone}`);
    stamp.append(icon(tone === "ok" ? "check" : tone === "bad" ? "close" : "history"), el("b", null, formatValue("status", value)));
    body.append(head, el("div", "tv-perforation"), stamp);
    return root;
  }
  function delivery({ args, payload }) {
    const r = payload.result || {};
    const a = day(r.window_start),
      b = day(r.window_end);
    const { root, body } = shell("delivery", payload, { kicker: s("order", { id: args.order_id ?? "—" }) });
    body.append(el("h5", "tv-title", `${dateText(r.window_start, { weekday: false })} – ${dateText(r.window_end, { weekday: false })}`));
    if (a && b) {
      const now = today();
      body.append(el("p", "tv-sub", now < a.stamp ? s("windowAhead", { n: Math.round((a.stamp - now) / 864e5) }) : now > b.stamp ? s("windowPast") : s("windowNow")));
      const first = Math.min(a.stamp, now) - 864e5,
        last = Math.max(b.stamp, now) + 864e5;
      body.append(
        drawing(46, (ctx, w, h, p, c) => {
          const left = 8,
            right = w - 8,
            y = 18;
          const x = (stamp) => left + ((stamp - first) / (last - first)) * (right - left);
          ctx.strokeStyle = c.strong;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(right, y);
          ctx.stroke();
          const from = x(a.stamp),
            to = x(b.stamp + 864e5);
          ctx.fillStyle = c.accent;
          roundRect(ctx, from, y - 6, Math.max(6, (to - from) * p), 12, 6);
          ctx.fill();
          const tx = x(now + 432e5);
          ctx.fillStyle = c.ink;
          ctx.beginPath();
          ctx.arc(tx, y, 4.5, 0, Math.PI * 2);
          ctx.fill();
          ctx.font = `500 9px ${c.ui}`;
          ctx.textAlign = "center";
          ctx.fillText(s("today"), tx, y + 20);
        }),
      );
    }
    return root;
  }
  function pass(payload, { label, id, amount, currency, dueKey, date }) {
    const n = daysUntil(date);
    const tone = n === null ? "neutral" : n < 0 ? "bad" : n <= 3 ? "warn" : "ok";
    const { root, body } = shell("pass", payload, { tone });
    const head = el("div", "tv-row tv-between");
    head.append(el("span", "tv-label", label), el("code", "tv-code", id ?? "—"));
    const due = el("p", `tv-due ${tone}`);
    due.append(el("span", null, s(dueKey, { date: dateText(date) })), el("b", null, n !== null && n < 0 ? s("overdue", { n: -n }) : relative(n)));
    body.append(head, el("p", "tv-amount", finite(amount) ? money(amount, currency) : "—"), el("div", "tv-perforation"), due);
    return root;
  }
  const bill = ({ args, payload }) =>
    pass(payload, {
      label: args.period ? `${s("bill")} · ${s("period", { period: args.period })}` : s("bill"),
      id: args.account_id,
      amount: payload.result?.amount,
      currency: payload.result?.currency,
      dueKey: "due",
      date: payload.result?.due,
    });
  const subscription = ({ args, payload }) =>
    pass(payload, {
      label: s("subscription"),
      id: args.subscription_id,
      amount: payload.result?.amount,
      currency: payload.result?.currency,
      dueKey: "renews",
      date: payload.result?.renews,
    });
  function warranty({ args, payload }) {
    const r = payload.result || {};
    const n = daysUntil(r.expires);
    const ok = n === null || n >= 0;
    const { root, body } = shell("warranty", payload, { tone: ok ? "ok" : "bad" });
    const head = el("div", "tv-row");
    const shield = el("span", `tv-shield ${ok ? "ok" : "bad"}`);
    shield.append(icon(ok ? "check" : "close"));
    const text = el("div");
    text.append(el("h5", "tv-title", s(ok ? "covered" : "expired")), el("p", "tv-sub", kick(s("until", { date: dateText(r.expires, { year: true }) }), relative(n))));
    head.append(shield, text);
    const scope = el("p", "tv-scope");
    scope.append(el("span", null, s("scope")), chip(r.coverage || "—"));
    body.append(head, scope, el("code", "tv-code", args.serial_number ?? ""));
    return root;
  }

  // ----- 出行 · travel ---------------------------------------------------------------------
  function weather({ args, payload }) {
    const r = payload.result || {};
    if (!finite(r.temperature_c)) return null;
    const pct = finite(r.rain_probability) ? Math.round(r.rain_probability * 100) : null;
    const { root, body } = shell("weather", payload, { kicker: kick(args.city, dateText(args.date)) });
    const row = el("div", "tv-row tv-between");
    row.append(figure(`${r.temperature_c}°`, payload.provenance?.temperature_semantics === "rounded_daily_mean_celsius" ? s("mean") : ""));
    if (pct !== null) {
      const rain = el("div", "tv-meter");
      const bar = el("i");
      bar.style.setProperty("--p", String(pct / 100));
      rain.append(el("span", null, s("rain")), el("b", null, `${pct}%`), bar);
      row.append(rain);
    }
    body.append(row, scale(r.temperature_c, -10, 40, [[0, "you"], [0.55, "accent"], [1, "warn"]], { left: "−10°", right: "40°" }));
    return root;
  }
  function daylight({ args, payload }) {
    const r = payload.result || {};
    const rise = clock(r.sunrise),
      set = clock(r.sunset);
    if (!rise || !set || set.minutes <= rise.minutes) return null;
    const { root, body } = shell("sun", payload, { kicker: kick(args.city, dateText(args.date)) });
    body.append(el("h5", "tv-title", s("daylight", { d: span(set.minutes - rise.minutes) })));
    // The sun is placed only when the date is today in the place's own time zone.
    const zone = payload.provenance?.timezone;
    let now = null;
    if (zone)
      try {
        const parts = Object.fromEntries(
          new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
            .formatToParts(new Date())
            .map((part) => [part.type, part.value]),
        );
        if (`${parts.year}-${parts.month}-${parts.day}` === args.date) now = +parts.hour * 60 + +parts.minute;
      } catch {}
    body.append(
      drawing(84, (ctx, w, h, p, c) => {
        const left = 10,
          right = w - 10,
          base = h - 22,
          peak = base - 46;
        const x = (m) => left + (m / 1440) * (right - left);
        const curve = (m) => base - Math.sin(Math.PI * Math.min(1, Math.max(0, (m - rise.minutes) / (set.minutes - rise.minutes)))) * (base - peak);
        ctx.strokeStyle = c.strong;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(left, base);
        ctx.lineTo(right, base);
        ctx.stroke();
        const end = rise.minutes + (set.minutes - rise.minutes) * p;
        const fill = ctx.createLinearGradient(0, peak, 0, base);
        fill.addColorStop(0, c.warn);
        fill.addColorStop(1, "transparent");
        ctx.beginPath();
        ctx.moveTo(x(rise.minutes), base);
        for (let m = rise.minutes; m <= end; m += 6) ctx.lineTo(x(m), curve(m));
        ctx.lineTo(x(end), base);
        ctx.closePath();
        ctx.globalAlpha = 0.28;
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = c.warn;
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let m = rise.minutes; m <= end; m += 6) m === rise.minutes ? ctx.moveTo(x(m), curve(m)) : ctx.lineTo(x(m), curve(m));
        ctx.stroke();
        ctx.font = `400 9.5px ${c.mono}`;
        ctx.fillStyle = c.dim;
        ctx.textAlign = "center";
        ctx.fillText(`↑ ${rise.text}`, x(rise.minutes), base + 15);
        ctx.fillText(`↓ ${set.text}`, x(set.minutes), base + 15);
        if (now !== null && p > 0.98) {
          const up = now > rise.minutes && now < set.minutes;
          ctx.fillStyle = up ? c.warn : c.faint;
          ctx.beginPath();
          ctx.arc(x(now), up ? curve(now) : base, 5, 0, Math.PI * 2);
          ctx.fill();
        }
      }),
    );
    return root;
  }
  function rail({ args, payload }) {
    const r = payload.result || {};
    const time = clock(r.departure);
    const { root, body } = shell("ticket", payload);
    const route = el("div", "tv-route");
    const line = el("div", "tv-route-line");
    line.append(el("i"), el("code", null, r.train || "—"), el("i"));
    route.append(el("b", "tv-station", args.origin ?? "—"), line, el("b", "tv-station", args.destination ?? "—"));
    const foot = el("div", "tv-row tv-between tv-ticket-foot");
    foot.append(el("span", "tv-sub", kick(dateText(args.date), relative(daysUntil(args.date)))), el("b", "tv-time", time ? time.text : r.departure || "—"));
    body.append(route, el("div", "tv-perforation"), foot);
    return root;
  }
  function bus({ args, payload }) {
    const minutes = payload.result?.minutes;
    const { root, body } = shell("bus", payload, { kicker: s("busKicker", { route: args.route ?? "—", stop: args.stop ?? "—" }) });
    body.append(figure(finite(minutes) ? String(minutes) : "—", s("minutesAway")));
    if (finite(minutes))
      body.append(
        // The bus sits proportionally on a 0–30 minute approach to the stop.
        drawing(34, (ctx, w, h, p, c) => {
          const left = 8,
            right = w - 14,
            y = 14;
          ctx.strokeStyle = c.strong;
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 4]);
          ctx.beginPath();
          ctx.moveTo(left, y);
          ctx.lineTo(right, y);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = c.ink;
          ctx.beginPath();
          ctx.arc(right, y, 5, 0, Math.PI * 2);
          ctx.fill();
          const share = 1 - Math.min(30, Math.max(0, minutes)) / 30;
          const bx = left + (right - left) * share * p;
          ctx.fillStyle = c.accent;
          roundRect(ctx, bx - 9, y - 6, 18, 12, 4);
          ctx.fill();
          ctx.font = `400 8.5px ${c.mono}`;
          ctx.fillStyle = c.faint;
          ctx.textAlign = "left";
          ctx.fillText("30'", left, y + 18);
          ctx.textAlign = "right";
          ctx.fillText(args.stop ? String(args.stop).slice(0, 8) : "", right + 6, y + 18);
        }),
      );
    return root;
  }
  function flight({ args, payload }) {
    const r = payload.result || {};
    const tone = mood(r.status);
    const delay = finite(r.delay_minutes) && r.delay_minutes > 0 ? s("delayed", { n: r.delay_minutes }) : "";
    return ticket(payload, {
      kicker: s("flightKicker"),
      title: args.flight,
      chips: [capsule(tone === "neutral" ? "warn" : tone, formatValue("status", r.status), tone === "ok" ? "check" : "history"), delay],
      date: args.date,
      tone: tone === "bad" ? "bad" : undefined,
    });
  }
  const parking = ({ args, payload }) =>
    counter(payload, { kicker: s("parkingKicker", { id: args.parking_id ?? "—" }), n: payload.result?.available_spaces, unit: s("unitSpaces") });
  const charging = ({ args, payload }) =>
    counter(payload, {
      kicker: kick(s("chargingKicker", { id: args.station_id ?? "—" }), args.connector),
      n: payload.result?.available_connectors,
      unit: s("unitConnectors"),
      shape: "round",
    });
  function road({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("road", payload, { kicker: s("roadKicker", { road: args.road ?? "—" }), tone: "warn" });
    const head = el("div", "tv-row tv-between");
    head.append(el("h5", "tv-title", r.segment || "—"), capsule("warn", s("closed"), "close"));
    body.append(head, el("p", "tv-sub", kick(s("reopens", { time: when(r.reopens_at) }), relative(daysUntil(r.reopens_at)))));
    return root;
  }

  // ----- 生活 · daily life -----------------------------------------------------------------
  const store = ({ args, payload }) =>
    hours(payload, { kicker: kick(s("storeKicker", { id: args.store_id ?? "—" }), dateText(args.date)), opens: payload.result?.opens, closes: payload.result?.closes, date: args.date });
  const stock = ({ args, payload }) =>
    counter(payload, { kicker: kick(s("storeKicker", { id: args.store_id ?? "—" }), args.sku), n: payload.result?.quantity, unit: s("unitItems") });
  function parcel({ args, payload }) {
    const r = payload.result || {};
    const tone = mood(r.status);
    const { root, body } = shell("parcel", payload, { kicker: s("parcelKicker", { id: args.tracking_id ?? "—" }) });
    const head = el("div", "tv-row tv-between");
    head.append(el("h5", "tv-title", formatValue("status", r.status)), capsule(tone === "neutral" ? "warn" : tone, formatValue("status", r.status), tone === "ok" ? "check" : "route"));
    const where = el("p", "tv-location");
    where.append(el("span", null, s("lastSeen")), el("b", null, r.last_location || "—"));
    body.append(head, where);
    return root;
  }
  const locker = ({ args, payload }) => hours(payload, { kicker: s("lockerKicker", { id: args.locker_id ?? "—" }), opens: payload.result?.opens, closes: payload.result?.closes });
  function waste({ args, payload }) {
    const next = payload.result?.next_collection;
    const { root, body } = shell("waste", payload, { kicker: kick(args.community, args.waste_type) });
    const row = el("div", "tv-row");
    row.append(bin(args.waste_type));
    const text = el("div", "tv-event");
    text.append(el("p", "tv-label", s("nextPickup")), el("h5", "tv-title", when(next)), el("p", "tv-sub", relative(daysUntil(next))));
    row.append(text);
    body.append(row);
    return root;
  }
  const water = ({ args, payload }) => outage(payload, { kicker: s("waterKicker", { address: args.address ?? "—" }), start: payload.result?.start, end: payload.result?.end });
  const power = ({ args, payload }) => outage(payload, { kicker: s("powerKicker", { address: args.address ?? "—" }), start: payload.result?.start, end: payload.result?.end });
  const laundry = ({ args, payload }) =>
    counter(payload, { kicker: kick(args.room, args.machine_type), n: payload.result?.available, unit: s("unitMachines"), shape: "round" });

  // ----- 文化 · culture --------------------------------------------------------------------
  const library = ({ args, payload }) =>
    counter(payload, { kicker: kick(args.branch, `ISBN ${args.isbn ?? "—"}`), n: payload.result?.available_copies, unit: s("unitCopies"), shape: "bar" });
  const museum = ({ args, payload }) =>
    ticket(payload, { kicker: s("museumKicker", { museum: args.museum ?? "—" }), title: payload.result?.exhibition, chips: [payload.result?.hall], date: args.date });
  const cinema = ({ args, payload }) =>
    ticket(payload, {
      kicker: s("cinemaKicker", { cinema: args.cinema ?? "—" }),
      title: args.movie,
      chips: [finite(payload.result?.screen) ? s("screen", { n: payload.result.screen }) : null],
      date: args.date,
      time: clock(payload.result?.showtime)?.text ?? payload.result?.showtime,
    });
  const theatre = ({ args, payload }) =>
    ticket(payload, {
      kicker: s("theatreKicker", { theatre: args.theatre ?? "—" }),
      title: payload.result?.performance,
      date: args.date,
      time: clock(payload.result?.starts)?.text ?? payload.result?.starts,
    });
  function match({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("match", payload, { kicker: s("matchKicker") });
    const sides = el("div", "tv-route");
    sides.append(el("b", "tv-station", args.team ?? "—"), el("span", "tv-versus", s("versus")), el("b", "tv-station", r.opponent || "—"));
    const foot = el("div", "tv-row tv-between tv-ticket-foot");
    foot.append(el("span", "tv-sub", kick(dateText(args.date), relative(daysUntil(args.date)))), el("b", "tv-time", clock(r.starts)?.text ?? r.starts ?? "—"));
    body.append(sides, el("div", "tv-perforation"), foot);
    return root;
  }
  const park = ({ args, payload }) =>
    status(payload, { kicker: kick(args.park, args.entrance), ok: payload.result?.open === true, label: s(payload.result?.open ? "open" : "closed"), title: args.entrance || args.park, reason: payload.result?.reason });
  function trail({ args, payload }) {
    const r = payload.result || {};
    const tone = mood(r.status);
    return status(payload, { kicker: s("trailKicker"), ok: tone === "ok" ? true : tone === "bad" ? false : null, label: formatValue("status", r.status), title: args.trail, reason: r.reason });
  }
  const pool = ({ args, payload }) =>
    counter(payload, { kicker: kick(args.pool, when(args.time)), n: payload.result?.public_lanes, unit: s("unitLanes"), shape: "lane" });

  // ----- 信息 · reference ------------------------------------------------------------------
  function currency({ args, payload }) {
    const rate = payload.result?.rate;
    if (!finite(rate) || rate <= 0 || !args.base || !args.quote) return null;
    const date = payload.provenance?.rate_date || args.date;
    const { root, body } = shell("fx", payload, { kicker: date ? s("rateDate", { date: dateText(date, { weekday: false }) }) : "" });
    const line = el("p", "tv-fx");
    line.append(el("span", null, `1 ${args.base} =`), el("b", null, String(Number(rate.toPrecision(6)))), el("span", null, args.quote));
    body.append(line, el("p", "tv-sub", `1 ${args.quote} = ${Number((1 / rate).toPrecision(4))} ${args.base}`));
    const table = el("div", "tv-convert");
    for (const amount of [10, 100, 1000]) {
      const cell = el("div");
      cell.append(el("span", null, money(amount, args.base)), el("b", null, money(Math.round(amount * rate * 100) / 100, args.quote)));
      table.append(cell);
    }
    body.append(table);
    if (payload.provenance?.not_transaction_quote) body.append(el("p", "tv-note", s("notQuote")));
    return root;
  }
  function postcode({ args, payload }) {
    const code = payload.result?.postcode;
    const { root, body } = shell("postcode", payload, { kicker: s("postcodeKicker") });
    body.append(el("p", "tv-address", kick(args.city, args.district, args.street)));
    const boxes = el("div", "tv-codebox");
    for (const digit of String(code ?? "—")) boxes.append(el("b", null, digit));
    body.append(boxes);
    return root;
  }
  function holiday({ args, payload }) {
    const on = payload.result?.is_holiday === true;
    const p = day(args.date);
    const { root, body } = shell("holiday", payload, { kicker: kick(args.region, relative(daysUntil(args.date))) });
    const tile = el("div", "tv-datetile");
    const part = (options) => (p ? new Intl.DateTimeFormat(locale(), { ...options, timeZone: "UTC" }).format(new Date(p.stamp)) : "");
    tile.append(el("span", "tv-datetile-week", part({ weekday: "short" })), el("b", null, p ? String(p.d) : "—"), el("span", "tv-datetile-month", part({ month: "short" })));
    const row = el("div", "tv-row");
    row.append(tile, capsule(on ? "ok" : "warn", s(on ? "holiday" : "notHoliday"), on ? "check" : "close"));
    body.append(row);
    return root;
  }
  function word({ args, payload }) {
    const { root, body } = shell("word", payload, { kicker: s("dictionaryKicker", { language: String(args.language || "—").toUpperCase() }) });
    body.append(el("h5", "tv-word", args.word ?? "—"), el("p", "tv-definition", payload.result?.definition || "—"));
    return root;
  }
  function pronunciation({ args, payload }) {
    const { root, body } = shell("word", payload, { kicker: s("pronunciationKicker", { language: String(args.language || "—").toUpperCase() }) });
    body.append(el("h5", "tv-word", args.name ?? "—"), el("p", "tv-ipa", payload.result?.ipa || "—"));
    return root;
  }
  function allergen({ args, payload }) {
    const raw = String(payload.result?.allergens ?? "").trim();
    const items = raw && !/^(none|无|-)$/i.test(raw) ? raw.split(/[,，、;；/]+/).map((item) => item.trim()).filter(Boolean) : [];
    const { root, body } = shell("allergen", payload, { kicker: s("allergenKicker", { sku: args.sku ?? "—" }), tone: items.length ? "warn" : "ok" });
    if (!items.length) body.append(capsule("ok", s("noAllergens")));
    else {
      body.append(el("p", "tv-label", s("contains")));
      const row = el("div", "tv-chips");
      for (const item of items) row.append(chip(formatValue("allergen", item), "tv-chip-warn"));
      body.append(row);
    }
    return root;
  }
  function ports({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("ports", payload, { kicker: s("deviceKicker", { model: args.model ?? "—" }) });
    for (const [label, n, shape] of [["USB‑C", r.usb_c_ports, "usbc"], ["HDMI", r.hdmi_ports, "hdmi"]]) {
      const row = el("div", "tv-row tv-between tv-port-row");
      const left = el("div", "tv-row");
      left.append(el("b", "tv-port-name", label), Number.isInteger(n) && n > 0 && n <= 12 ? dots(n, shape) : el("span"));
      row.append(left, el("b", "tv-count", finite(n) ? `× ${n}` : "—"));
      body.append(row);
    }
    return root;
  }

  // ----- 环境 · environment ----------------------------------------------------------------
  function air({ args, payload }) {
    const r = payload.result || {};
    if (!finite(r.aqi)) return null;
    const { root, body } = shell("air", payload, { kicker: s("airKicker", { station: args.station ?? "—" }) });
    body.append(figure(String(r.aqi), "AQI", el("em", "tv-grade", formatValue("category", r.category))));
    // Index breakpoints 50/100/150/200/300 (shared by HJ 633 and the US AQI) on a 0–500 scale.
    body.append(
      scale(r.aqi, 0, 500, [[0, "#4cc38a"], [0.1, "#4cc38a"], [0.2, "#e9c46a"], [0.3, "#f4a259"], [0.4, "#e76f51"], [0.6, "#9d6bc8"], [1, "#8a3b4a"]], {
        left: "0",
        right: "500",
        marks: [[100, "100"], [200, "200"], [300, "300"]],
      }),
    );
    return root;
  }
  function pollen({ args, payload }) {
    const level = String(payload.result?.level || "").toLowerCase();
    const steps = ["low", "medium", "high", "very_high"];
    const index = steps.indexOf(level);
    const { root, body } = shell("pollen", payload, { kicker: kick(args.region, dateText(args.date)) });
    body.append(el("h5", "tv-title", formatValue("level", payload.result?.level)));
    const meter = el("div", "tv-steps");
    steps.forEach((step, i) => {
      const cell = el("i");
      if (index >= 0 && i <= index) cell.className = `on s${index}`;
      meter.append(cell);
    });
    body.append(meter);
    return root;
  }
  function uv({ args, payload }) {
    const value = payload.result?.uv_index;
    if (!finite(value)) return null;
    // WHO UV index categories.
    const band = value <= 2 ? "uvLow" : value <= 5 ? "uvModerate" : value <= 7 ? "uvHigh" : value <= 10 ? "uvVeryHigh" : "uvExtreme";
    const { root, body } = shell("uv", payload, { kicker: kick(args.city, when(args.time)) });
    body.append(figure(String(value), "UV", el("em", "tv-grade", s(band))));
    body.append(scale(value, 0, 12, [[0, "#4cc38a"], [0.2, "#e9c46a"], [0.45, "#f4a259"], [0.65, "#e76f51"], [1, "#9d6bc8"]], { left: "0", right: "11+" }));
    return root;
  }
  function river({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("river", payload, { kicker: s("riverKicker", { station: args.station ?? "—" }) });
    body.append(figure(finite(r.level_m) ? number(r.level_m, 2) : "—", "m"), el("p", "tv-sub", kick(s("measured", { time: when(r.measured_at) }), relative(daysUntil(r.measured_at)))));
    return root;
  }
  function tide({ args, payload }) {
    const time = clock(payload.result?.high_tide);
    const { root, body } = shell("tide", payload, { kicker: kick(args.beach, dateText(args.date)) });
    body.append(figure(time?.text ?? payload.result?.high_tide ?? "—", s("highTide")));
    if (time) body.append(dayStrip({ pins: [{ minutes: time.minutes, color: "you" }], now: daysUntil(args.date) === 0 }));
    return root;
  }
  function noise({ args, payload }) {
    const r = payload.result || {};
    if (!finite(r.decibels)) return null;
    const { root, body } = shell("noise", payload, { kicker: s("noiseKicker", { station: args.station ?? "—" }) });
    body.append(figure(String(r.decibels), "dB"), scale(r.decibels, 30, 110, [[0, "#4cc38a"], [0.35, "#e9c46a"], [0.65, "#f4a259"], [1, "#e76f51"]], { left: "30", right: "110", marks: [[85, "85"]] }));
    body.append(el("p", "tv-sub", kick(s("measured", { time: when(r.measured_at) }), relative(daysUntil(r.measured_at)))));
    return root;
  }
  /** The four sorting bins of GB/T 19095-2019 by their standard colours. */
  function bin(category) {
    const text = String(category || "");
    const kind = /可回收|recycl/i.test(text) ? "blue" : /有害|hazard/i.test(text) ? "red" : /厨余|湿|food|kitchen/i.test(text) ? "green" : /其他|干|residual|other/i.test(text) ? "grey" : "none";
    const node = el("span", `tv-bin ${kind}`);
    node.append(el("i"), el("b", null, text || "—"));
    return node;
  }
  function recycling({ args, payload }) {
    const { root, body } = shell("recycling", payload, { kicker: kick(args.city) });
    const row = el("div", "tv-row");
    const text = el("div", "tv-event");
    text.append(el("p", "tv-label", s("item")), el("h5", "tv-title", args.item ?? "—"));
    row.append(text, el("span", "tv-arrow", "→"), bin(payload.result?.category));
    body.append(row);
    return root;
  }
  function alert({ args, payload }) {
    const active = payload.result?.active === true;
    const { root, body } = shell("alert", payload, { kicker: s("alertKicker", { region: args.region ?? "—" }), tone: active ? "bad" : "ok" });
    const banner = el("div", `tv-banner ${active ? "bad" : "ok"}`);
    banner.append(icon(active ? "bell" : "check"), el("b", null, s(active ? "alertOn" : "alertOff")));
    body.append(banner);
    return root;
  }

  // ----- 设施 · facilities -----------------------------------------------------------------
  function access({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("access", payload, { kicker: kick(args.venue, args.entrance) });
    const grid = el("div", "tv-tiles");
    for (const [key, value] of [["ramp", r.ramp], ["elevator", r.elevator]]) {
      const tile = el("div", `tv-tile ${value ? "ok" : "bad"}`);
      tile.append(icon(value ? "check" : "close"), el("b", null, s(key)), el("span", null, s(value ? "available" : "unavailable")));
      grid.append(tile);
    }
    body.append(grid);
    return root;
  }
  function wifi({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("wifi", payload, { kicker: s("wifiKicker", { venue: args.venue ?? "—" }) });
    const row = el("div", "tv-row");
    const glyph = el("span", "tv-wifi");
    glyph.append(el("i"), el("i"), el("i"), el("b"));
    const text = el("div", "tv-event");
    text.append(el("h5", "tv-title tv-mono-title", r.ssid || "—"), el("p", "tv-sub", formatValue("method", r.method)));
    row.append(glyph, text);
    body.append(row);
    return root;
  }
  const lostfound = ({ args, payload }) => hours(payload, { kicker: args.office, opens: payload.result?.opens, closes: payload.result?.closes });
  function restroom({ args, payload }) {
    const { root, body } = shell("restroom", payload, { kicker: s("restroomKicker", { station: args.station ?? "—" }) });
    const where = el("p", "tv-location");
    where.append(el("span", null, s("location")), el("b", null, payload.result?.location || "—"));
    body.append(where);
    return root;
  }
  const luggage = ({ args, payload }) => counter(payload, { kicker: args.location, n: payload.result?.large_lockers, unit: s("unitLockers"), shape: "locker" });
  function ferry({ args, payload }) {
    const r = payload.result || {};
    const running = r.operating === true;
    const { root, body } = shell("ferry", payload, { kicker: s("ferryKicker", { terminal: args.terminal ?? "—" }), tone: running ? undefined : "bad" });
    const head = el("div", "tv-row tv-between");
    head.append(figure(clock(r.next_departure)?.text ?? r.next_departure ?? "—", s("nextDeparture")), capsule(running ? "ok" : "bad", s(running ? "running" : "suspended")));
    body.append(head);
    return root;
  }
  const bike = ({ args, payload }) => counter(payload, { kicker: s("bikeKicker", { station: args.station ?? "—" }), n: payload.result?.empty_docks, unit: s("unitDocks"), shape: "round" });
  function canteen({ args, payload }) {
    const r = payload.result || {};
    const { root, body } = shell("menu", payload, { kicker: kick(args.canteen, dateText(args.date), formatValue("meal", args.meal)) });
    body.append(el("p", "tv-label", s("main")), el("h5", "tv-dish", r.main || "—"), el("p", "tv-label", s("side")), el("p", "tv-side", r.side || "—"));
    return root;
  }

  // ----- live market dashboards (runtime tools added with this frontend) -----------------------
  function marketState(state) {
    return state === "open" ? s("marketOpen") : state === "closed" ? s("marketClosed") : "";
  }
  function quote({ args, payload }) {
    const r = payload.result || {};
    if (!finite(r.price)) return null;
    // HK/ETF and low-priced securities may have meaningful sub-cent digits.
    // Keep provider precision instead of unconditionally rounding to 2 places.
    const quoteNumber = value => finite(value)
      ? new Intl.NumberFormat(locale(), {minimumFractionDigits:2, maximumFractionDigits:6}).format(value)
      : "—";
    const direction = trend(r.change);
    const asked = String(args.query ?? "").trim();
    const { root, body } = shell("quote", payload, { kicker: kick(r.symbol, r.currency, marketState(r.market_state)) });
    const head = el("div", "tv-row tv-between");
    const title = el("div", "tv-event");
    title.append(el("h5", "tv-title", r.name || r.symbol || "—"));
    if (asked && asked !== r.name && asked !== r.symbol) title.append(el("p", "tv-sub", s("matched", { query: asked })));
    head.append(title);
    const price = el("div", "tv-price");
    const delta = el("span", `tv-delta ${direction}`);
    delta.append(el("b", null, signed(r.change)), el("b", null, `${signed(r.change_pct)}%`));
    price.append(el("b", null, quoteNumber(r.price)), delta);
    head.append(price);
    body.append(head);
    const chart = payload.provenance?.chart;
    const slots = { cn: 242, hk: 332, us: 391 }[r.market];
    const canvas = Array.isArray(chart?.points) ? priceChart(chart.points, { baseline: chart.prev_close ?? r.prev_close, slots }) : null;
    if (canvas) body.append(canvas);
    const stats = el("div", "tv-stats");
    for (const [key, value] of [["statOpen", r.open], ["statHigh", r.high], ["statLow", r.low], ["statPrevClose", r.prev_close]]) stats.append(pair(s(key), quoteNumber(value)));
    body.append(stats, el("p", "tv-note", kick(r.as_of ? s("asOf", { time: when(r.as_of) }) : "", s("notAdvice"))));
    return root;
  }
  function indices({ args, payload }) {
    const r = payload.result || {};
    const rows = Array.isArray(r.indices) ? r.indices : [];
    if (!rows.length) return null;
    const { root, body } = shell("indices", payload, { kicker: kick(s("region_" + (r.region || args.region)) || r.region, marketState(r.market_state)) });
    const grid = el("div", "tv-board");
    for (const row of rows) {
      const tile = el("div", `tv-board-tile ${trend(row.change_pct)}`);
      tile.append(el("span", null, row.name || row.symbol), el("b", null, number(row.price)), el("em", null, `${signed(row.change_pct)}%`));
      const chart = payload.provenance?.charts?.[row.symbol];
      const spark = Array.isArray(chart?.points) ? priceChart(chart.points, { baseline: chart.prev_close, slots: { cn: 242, hk: 332, us: 391 }[r.region], height: 34, labels: false }) : null;
      if (spark) tile.append(spark);
      grid.append(tile);
    }
    body.append(grid, el("p", "tv-note", kick(r.as_of ? s("asOf", { time: when(r.as_of) }) : "", s("notAdvice"))));
    return root;
  }
  function fxTrend({ args, payload }) {
    const r = payload.result || {};
    if (!finite(r.last)) return null;
    const series = Array.isArray(payload.provenance?.series) ? payload.provenance.series : [];
    const { root, body } = shell("trend", payload, { kicker: s("trendKicker", { base: r.base || args.base, quote: r.quote || args.quote, days: args.days ?? "—" }) });
    const head = el("div", "tv-row tv-between");
    const value = el("p", "tv-fx");
    value.append(el("span", null, `1 ${r.base || args.base} =`), el("b", null, String(Number(r.last.toPrecision(6)))), el("span", null, r.quote || args.quote));
    const delta = el("span", `tv-delta ${trend(r.change_pct)}`);
    delta.append(el("b", null, `${signed(r.change_pct)}%`));
    head.append(value, delta);
    body.append(head);
    const labelled = series.map(([date, rate]) => [dateText(date, { weekday: false }), rate]);
    const canvas = labelled.length > 1 ? priceChart(labelled, { baseline: r.first }) : null;
    if (canvas) body.append(canvas);
    const stats = el("div", "tv-stats");
    stats.append(
      pair(s("statHigh"), `${number(r.high, 4)} · ${dateText(r.high_date, { weekday: false })}`),
      pair(s("statLow"), `${number(r.low, 4)} · ${dateText(r.low_date, { weekday: false })}`),
    );
    body.append(stats, el("p", "tv-note", kick(`${dateText(r.start, { weekday: false })} – ${dateText(r.end, { weekday: false })}`, s("notQuote"))));
    return root;
  }

  // Wall-clock world clock and the remaining reference views.
  function worldClock({ args, payload }) {
    const offset = payload.result?.offset_minutes;
    const instant = Date.parse(args.instant || "");
    if (!finite(offset) || !finite(instant)) return null;
    const asked = clock(args.instant);
    const zone = /(Z|[+-]\d{2}:?\d{2})$/.exec(args.instant || "")?.[1] || "";
    const { root, body } = shell("clock", payload, { kicker: kick(dateText(args.instant), asked ? `${asked.text} ${zone}`.trim() : "") });
    const local = new Date(instant + offset * 60000);
    const hh = local.getUTCHours(),
      mm = local.getUTCMinutes();
    const diff = offset - -new Date(instant).getTimezoneOffset();
    const row = el("div", "tv-row");
    row.append(
      drawing(
        88,
        (ctx, w, h, p, c) => {
          const r = 40,
            cx = r + 4,
            cy = h / 2;
          ctx.strokeStyle = c.strong;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(cx, cy, r, 0, Math.PI * 2);
          ctx.stroke();
          for (let i = 0; i < 12; i++) {
            const a = (i / 12) * Math.PI * 2;
            const inner = i % 3 ? r - 4 : r - 7;
            ctx.strokeStyle = i % 3 ? c.faint : c.dim;
            ctx.lineWidth = i % 3 ? 1 : 1.5;
            ctx.beginPath();
            ctx.moveTo(cx + Math.sin(a) * inner, cy - Math.cos(a) * inner);
            ctx.lineTo(cx + Math.sin(a) * (r - 1.5), cy - Math.cos(a) * (r - 1.5));
            ctx.stroke();
          }
          // Hands sweep from twelve to the local time of the requested instant.
          const hand = (turns, length, width) => {
            const a = turns * Math.PI * 2 * p;
            ctx.strokeStyle = c.ink;
            ctx.lineWidth = width;
            ctx.lineCap = "round";
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(cx + Math.sin(a) * length, cy - Math.cos(a) * length);
            ctx.stroke();
          };
          hand(((hh % 12) + mm / 60) / 12, r * 0.52, 2.5);
          hand(mm / 60, r * 0.78, 1.5);
          ctx.fillStyle = c.accent;
          ctx.beginPath();
          ctx.arc(cx, cy, 3, 0, Math.PI * 2);
          ctx.fill();
        },
        { className: "tv-clockface" },
      ),
    );
    const text = el("div", "tv-event");
    const sign = offset < 0 ? "−" : "+";
    text.append(
      el("h5", "tv-title", args.city ?? "—"),
      el("p", "tv-time", `${pad(hh)}:${pad(mm)}`),
      el("p", "tv-sub", kick(`UTC${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`, diff === 0 ? s("same") : s(diff > 0 ? "ahead" : "behind", { d: span(Math.abs(diff)) }))),
    );
    row.append(text);
    body.append(row);
    return root;
  }

  const VIEWS = {
    // 个人
    "calendar.agenda": calendar,
    "meeting.room": room,
    "leave.balance": leave,
    "expense.status": expense,
    "order.delivery": delivery,
    "utility.bill": bill,
    "subscription.renewal": subscription,
    "warranty.coverage": warranty,
    // 出行
    "weather.day": weather,
    "sun.daylight": daylight,
    "rail.departures": rail,
    "bus.next_arrival": bus,
    "flight.status": flight,
    "parking.vacancy": parking,
    "charging.connector": charging,
    "road.closure": road,
    // 生活
    "store.hours": store,
    "stock.item": stock,
    "parcel.status": parcel,
    "locker.hours": locker,
    "waste.collection": waste,
    "water.outage": water,
    "power.outage": power,
    "laundry.machine": laundry,
    // 文化
    "library.copy": library,
    "museum.exhibition": museum,
    "cinema.showtime": cinema,
    "theatre.performance": theatre,
    "sports.fixture": match,
    "park.access": park,
    "trail.status": trail,
    "pool.lanes": pool,
    // 信息
    "currency.reference": currency,
    "postcode.address": postcode,
    "timezone.offset": worldClock,
    "holiday.calendar": holiday,
    "word.definition": word,
    "name.pronunciation": pronunciation,
    "food.allergen": allergen,
    "device.port": ports,
    // 环境
    "air.quality": air,
    "pollen.level": pollen,
    "uv.index": uv,
    "river.level": river,
    "beach.tide": tide,
    "noise.measurement": noise,
    "recycling.category": recycling,
    "alert.bulletin": alert,
    // 设施
    "venue.accessibility": access,
    "wifi.access": wifi,
    "lostfound.hours": lostfound,
    "restroom.facility": restroom,
    "luggage.capacity": luggage,
    "ferry.operation": ferry,
    "bike.docks": bike,
    "canteen.menu": canteen,
    // Live market dashboards
    "stock.quote": quote,
    "market.indices": indices,
    "currency.trend": fxTrend,
    "calendar.create":personalCalendar,"calendar.update":personalCalendar,"calendar.delete":personalCalendar,"calendar.restore":personalCalendar,
    "task.list":personalTasks,"task.create":personalTasks,"task.update":personalTasks,"task.delete":personalTasks,"task.restore":personalTasks,
  };
  // currency.reference keeps its argument chips: the requested date and the reference date can differ.
  const KEEP_ARGUMENTS = new Set(["currency.reference"]);
  return {
    has: (tool) => tool in VIEWS,
    /** True when the module shows every argument in context, so the chips can fold away. */
    covers: (tool) => tool in VIEWS && !KEEP_ARGUMENTS.has(tool),
    /** A module for this record, or null to keep the generic result box. */
    render(record) {
      const view = VIEWS[record.tool];
      if (!view || !record.payload) return null;
      try {
        return view({ args: record.args || {}, payload: record.payload });
      } catch (error) {
        console.warn("toolview failed", record.tool, error);
        return null;
      }
    },
    /** Redraw every canvas at rest, e.g. after a theme change. */
    repaint() {
      for (const entry of drawings) {
        if (!entry.node.isConnected) drawings.delete(entry);
        else entry.render(1);
      }
    },
  };
}
