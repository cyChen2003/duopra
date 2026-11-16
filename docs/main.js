/* Duopra site — replay of recorded sessions, streamed as they are spoken.
   The trimmed recording is the clock: the audio is the master and the page
   follows it. Speech windows are measured from the recordings and tool-call
   times are the recorded ones; words inside a window are spread evenly across
   it, which is a reading aid, not a word-level alignment.

   Around the replay: the hero orb (the deployed app's shader, driven by the
   first recording), the demo-video slot, reveal on arrival, count-up figures,
   a scroll-linked headline, section spy, chart tooltips, copy buttons and the
   small-screen menu. Page motion runs once, is driven by transform / opacity /
   clip-path, and is replaced by its end state under prefers-reduced-motion;
   the orb is the one continuous motion, drawn only while it is on screen. */
(() => {
"use strict";

const ZH   = (document.documentElement.lang || "en").startsWith("zh");
const BASE = ZH ? "../" : "";            /* /zh/ sits one level down */
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)");
const L = ZH ? {
  you:"你", agent:"Duopra", listen:"聆听", speak:"说话", act:"调用工具",
  idle:"待机", end:"结束", play:"按录音时间播放", pause:"暂停", replay:"重新播放",
  silent:"不出声", notice:"值得注意", userAudio:"用户录音（FDB v3）",
  agentAudio:"Duopra 输出录音", seed:"种子", listenTip:"用户在说", speakTip:"Duopra 在说",
  actTip:"动作已发出", result:"返回",
  blocked:"浏览器拦截了播放，请再点一次",
  startHint:"按下播放。文字随说话逐字出现，每次工具调用在发出的那一刻出现。",
  copy:"复制", copied:"已复制", action:"动作",
  orbPlay:"播放这段会话", orbPause:"暂停", orbResume:"继续播放", orbAgain:"再听一遍",
  orbIdle:"真实录音", orbListen:"聆听中", orbSpeak:"说话中", orbCall:"调用工具",
  orbPaused:"已暂停", orbEnd:"会话结束", never:"不出声", sep:" · ",
  loading:"正在加载录音…", orbLoading:"正在加载录音"
} : {
  you:"You", agent:"Duopra", listen:"Listening", speak:"Speaking", act:"Calling a tool",
  idle:"Idle", end:"End", play:"Play in recorded time", pause:"Pause", replay:"Play again",
  silent:"never voiced", notice:"What to notice", userAudio:"User recording (FDB v3)",
  agentAudio:"Duopra output recording", seed:"seed", listenTip:"the user is speaking",
  speakTip:"Duopra is speaking", actTip:"an action went out", result:"returned",
  blocked:"The browser blocked playback — press play again",
  startHint:"Press play. The words appear as they are spoken, and each tool call appears the moment it is sent.",
  copy:"Copy", copied:"Copied", action:"Action",
  orbPlay:"Play the session", orbPause:"Pause", orbResume:"Resume", orbAgain:"Play again",
  orbIdle:"Recorded session", orbListen:"Listening", orbSpeak:"Speaking", orbCall:"Calling a tool",
  orbPaused:"Paused", orbEnd:"End of session", never:"never voiced", sep:" · ",
  loading:"Loading the recording…", orbLoading:"Loading the recording"
};

const el = (tag, cls, txt) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
};
/* latin streams by word, cjk by character */
const pieces = s => ZH ? Array.from(s) : (s.match(/\S+\s*/g) || []);
const mmss = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
/* How far the two tracks may drift apart before the assistant is pulled back.
   Over a network a seek costs a round trip; below a few hundred ms the pull
   itself would make the drift, so it is corrected rarely and never mid-seek. */
const DRIFT = 0.25;
/* both tracks can play on (HAVE_FUTURE_DATA) and neither is seeking */
const bothReady = (u, a) => u.readyState >= 3 && a.readyState >= 3 && !u.seeking && !a.seeking;
/* only a refusal by the browser stops playback; our own pause while waiting
   for data interrupts play() with an AbortError, which is not a refusal */
const refused = rs => rs.some(r => r.status === "rejected" && r.reason && r.reason.name === "NotAllowedError");

/* bar heights: one character per bar, height = charCode - 32 over 0..94 */
function barRects(peaks, width = .76) {
  const out = [];
  for (let i = 0; i < peaks.length; i++) {
    const v = Math.max(1.5, (peaks.charCodeAt(i) - 32) / 94 * 92);
    out.push(`<rect x="${(i + (1 - width) / 2).toFixed(2)}" y="${((100 - v) / 2).toFixed(2)}" width="${width}" height="${v.toFixed(2)}"/>`);
  }
  return out.join("");
}

/* one <svg> of 240 bars; the played part is a clipped copy in full colour */
let uid = 0;
function waveform(peaks, cls) {
  const N = peaks.length, id = "clip" + (++uid);
  const body = barRects(peaks);
  return `<svg viewBox="0 0 ${N} 100" preserveAspectRatio="none" aria-hidden="true">
    <defs><clipPath id="${id}"><rect class="pr" x="0" y="0" width="0" height="100"/></clipPath></defs>
    <g class="lane-dim ${cls}" fill="currentColor">${body}</g>
    <g class="${cls}" fill="currentColor" clip-path="url(#${id})">${body}</g>
  </svg>`;
}

class Scene {
  constructor(root, cases) {
    this.root = root; this.cases = cases;
    this.i = -1; this.t = 0; this.raf = 0; this.playing = false; this.withAudio = false;
    this.build();
    this.load(0);
  }

  build() {
    /* picker */
    this.picker = el("div", "picker");
    this.picker.setAttribute("role", "tablist");
    this.cases.forEach((c, k) => {
      const x = c[ZH ? "zh" : "en"];
      const b = el("button", "pick");
      b.type = "button"; b.setAttribute("role", "tab");
      b.innerHTML =
        `<span class="pick-cover"><span class="who">${L.you}</span>` +
        `<span class="quote">${x.quote}</span></span>` +
        `<span class="pick-body"><span class="pick-meta"><span>${x.index} / ${x.kind}</span>` +
        `<span>${mmss(c.duration)}</span></span><strong></strong><span class="desc"></span></span>`;
      b.querySelector("strong").textContent = x.cover;
      b.querySelector(".desc").textContent = x.blurb;
      b.onclick = () => { this.load(k); this.start(true); };
      this.picker.append(b);
    });

    /* scene shell */
    this.sTitle = el("span", "title");
    this.sMeta  = el("span");
    this.clock  = el("span", "clock", "0.0 s");
    const top = el("div", "scene-top"); top.append(this.sTitle, this.sMeta, this.clock);

    this.lanes = el("div", "lanes");
    this.chips = el("div", "chips");
    this.chipNodes = {};
    [["listen", L.listen], ["speak", L.speak], ["call", L.act]].forEach(([k, label]) => {
      const c = el("div", "chip " + k);
      c.append(el("span", "bulb"), el("span", null, label));
      c.dataset.on = "0";
      this.chips.append(c);
      this.chipNodes[k] = c;
    });

    this.playBtn = el("button", "btn btn-primary play", L.play);
    this.playBtn.type = "button";
    this.playBtn.onclick = () => this.playing ? this.pause() : this.start(true);
    this.scrub = document.createElement("input");
    this.scrub.type = "range"; this.scrub.className = "scrub";
    this.scrub.min = 0; this.scrub.step = 0.05; this.scrub.value = 0;
    this.scrub.setAttribute("aria-label", ZH ? "播放进度" : "Playback position");
    this.scrub.oninput = () => this.seek(parseFloat(this.scrub.value));
    this.time = el("span", "time", "0:00 / 0:00");
    this.hint = el("span", "hint"); this.hint.hidden = true;
    this.hint.setAttribute("role", "status");
    const tr = el("div", "transport"); tr.append(this.playBtn, this.scrub, this.time, this.hint);

    this.chapters = el("div", "chapters");

    this.stream = el("div", "stream");
    this.aside = el("div", "aside");
    const body = el("div", "scene-body"); body.append(this.stream, this.aside);

    this.box = el("div", "scene");
    this.box.append(top, this.lanes, this.chips, tr, this.chapters, body);
    this.root.append(this.picker, this.box);
  }

  load(k) {
    if (k === this.i) return;
    this.stop();
    this.i = k;
    const c = this.cases[k], x = c[ZH ? "zh" : "en"];
    [...this.picker.children].forEach((b, j) => b.setAttribute("aria-selected", String(j === k)));

    this.sTitle.textContent = x.title;
    this.sMeta.textContent = `FDB v3 · ${L.seed} ${c.seed} · ${c.duration.toFixed(1)} s`;
    this.scrub.max = c.duration;

    /* lanes */
    const peaks = (window.DUOPRA_PEAKS || {})[c.id] || { user: "", assistant: "" };
    this.lanes.textContent = "";
    this.cursors = [];
    const mk = (cls, name, p) => {
      const row = el("div", "lane " + cls);
      row.append(el("div", "lane-name", name));
      const box = el("div", "lane-box");
      box.innerHTML = waveform(p, cls);
      const cur = el("div", "cursor");
      box.append(cur);
      this.cursors.push(cur);
      row.append(box);
      this.lanes.append(row);
      return box;
    };
    this.uBox = mk("u", L.you, peaks.user);
    this.aBox = mk("a", L.agent, peaks.assistant);

    const trow = el("div", "toolrow");
    trow.append(el("div", "lane-name", ZH ? "动作" : "Actions"));
    const tline = el("div", "toolline");
    tline.append(el("div", "axis"));
    this.marks = c.calls.map(call => {
      const m = el("div", "tool-mark");
      m.style.left = (call.at / c.duration * 100) + "%";
      m.append(el("span", "dot"), el("span", "lbl", call.fn + "()"));
      tline.append(m);
      return m;
    });
    const tcur = el("div", "cursor");
    tline.append(tcur);
    this.cursors.push(tcur);
    trow.append(tline);
    this.lanes.append(trow);

    const axis = el("div", "lane-axis");
    axis.append(el("span", null, "0:00"), el("span", null, mmss(c.duration)));
    this.lanes.append(axis);

    /* chapters */
    this.chapters.textContent = "";
    c.chapters.forEach(ch => {
      const b = el("button", ch.key ? "key" : "");
      b.type = "button";
      b.append(el("span", "ct", mmss(ch.at)), el("span", null, ch[ZH ? "zh" : "en"]));
      b.onclick = () => { this.seek(ch.at); if (!this.playing) this.start(true); };
      this.chapters.append(b);
    });

    /* aside */
    this.aside.textContent = "";
    this.aside.append(el("span", "kicker", L.notice), el("p", null, x.takeaway));
    const audio = el("div", "sep audio-row");
    this.media = {};
    [["user", L.userAudio], ["assistant", L.agentAudio]].forEach(([who, label]) => {
      const a = document.createElement("audio");
      a.controls = true; a.preload = "metadata"; a.src = BASE + c.audio[who];
      /* the players share the scene's clock: their play, pause and seek move
         the scene, and the scene's own button moves them. A player that ran on
         its own would leave the transcript and the bars behind the sound. */
      a.addEventListener("play", () => {
        if (this.playing) return;
        this.seek(a.currentTime, true);
        this.start(true);
      });
      a.addEventListener("pause", () => { if (this.playing && !this.holding) this.pause(); });
      a.addEventListener("seeked", () => {
        if (this.ownSeek.delete(a)) return;              /* our own alignment, not the visitor's */
        if (Math.abs(a.currentTime - this.t) > .02) this.seek(a.currentTime, true);
      });
      audio.append(el("label", null, label), a);
      this.media[who] = a;
    });
    this.aside.append(audio);
    this.ownSeek = new Set();
    this.holding = false;

    this.reset();
    this.showAll();
  }

  /* the prompt that stands in for the transcript until play; it leaves as soon
     as the first turn or call appears */
  clearHint() {
    const h = this.stream.querySelector(".stream-hint");
    if (h) h.remove();
  }

  reset() {
    const c = this.cases[this.i];
    this.t = 0;
    this.stream.textContent = "";
    this.stream.append(el("p", "stream-hint", L.startHint));
    this.nodes = new Map();
    this.timeline = [];
    c.user.forEach((s, j)      => this.timeline.push({ kind: "turn", who: "u", seg: s, key: "u" + j }));
    c.assistant.forEach((s, j) => this.timeline.push({ kind: "turn", who: "a", seg: s, key: "a" + j }));
    c.calls.forEach((k, j)     => this.timeline.push({ kind: "act", call: k, key: "c" + j }));
    this.timeline.sort((p, q) =>
      (p.kind === "act" ? p.call.at : p.seg.t[0]) - (q.kind === "act" ? q.call.at : q.seg.t[0]));
    this.render(0);
  }

  turnNode(item) {
    if (this.nodes.has(item.key)) return this.nodes.get(item.key);
    this.clearHint();
    const row = el("div", "turn " + item.who);
    const head = el("div", "who");
    head.append(el("b", null, item.who === "u" ? L.you : L.agent),
                el("span", null, `${item.seg.t[0].toFixed(1)}–${item.seg.t[1].toFixed(1)} s`));
    const say = el("div", "say");
    row.append(head, say);
    this.stream.append(row);
    const rec = { say, parts: pieces(item.seg[ZH ? "zh" : "en"]), shown: -1 };
    this.nodes.set(item.key, rec);
    return rec;
  }

  actNode(item) {
    if (this.nodes.has(item.key)) return this.nodes.get(item.key);
    this.clearHint();
    const c = item.call;
    const box = el("div", "act");
    const h = el("div", "act-h");
    h.append(el("span", null, L.act), el("b", null, c.fn + "()"), el("span", "silent", L.silent));
    const args = Object.entries(c.args).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("   ");
    const ret = el("div", "ret");
    ret.append(el("pre", null, "→ " + Object.entries(c.result).map(([k, v]) => `${k}: ${v}`).join("   ")));
    ret.hidden = true;
    box.append(h, el("pre", null, args), ret);
    this.stream.append(box);
    const rec = { ret };
    this.nodes.set(item.key, rec);
    return rec;
  }

  render(t) {
    const c = this.cases[this.i];
    this.t = t;
    this.clock.textContent = t.toFixed(1) + " s";
    this.time.textContent = `${mmss(t)} / ${mmss(c.duration)}`;
    if (document.activeElement !== this.scrub) this.scrub.value = t;

    const pct = t / c.duration;
    this.cursors.forEach(n => n.style.left = (pct * 100) + "%");
    [this.uBox, this.aBox].forEach(b => {
      const r = b.querySelector("rect.pr");
      if (r) r.setAttribute("width", (pct * 240).toFixed(2));
    });

    let speaking = false, listening = false, acting = false;
    for (const item of this.timeline) {
      if (item.kind === "act") {
        if (t >= item.call.at) {
          const a = this.actNode(item);
          if (t >= item.call.at + 0.9) a.ret.hidden = false;
          if (t < item.call.at + 1.1) acting = true;
        }
        continue;
      }
      const [a, b] = item.seg.t;
      if (t < a) continue;
      const rec = this.turnNode(item);
      const frac = Math.min(1, (t - a) / Math.max(0.3, b - a));
      const want = Math.max(0, Math.floor(frac * rec.parts.length));
      if (want !== rec.shown) {
        rec.shown = want;
        rec.say.textContent = rec.parts.slice(0, want).join("");
        if (frac < 1 && this.playing) rec.say.append(el("span", "caret"));
      }
      if (t <= b) { if (item.who === "a") speaking = true; else listening = true; }
    }
    this.marks.forEach((m, j) => m.classList.toggle("fired", t >= c.calls[j].at));
    if (this.playing) this.stream.scrollTop = this.stream.scrollHeight;
    this.chipNodes.listen.dataset.on = listening ? "1" : "0";
    this.chipNodes.speak.dataset.on  = speaking  ? "1" : "0";
    this.chipNodes.call.dataset.on   = acting    ? "1" : "0";
  }

  /* jump the scene to t; when t came from the audio itself, do not write it
     back (that would re-fire the players' seeked events) */
  seek(t, fromAudio = false) {
    const c = this.cases[this.i];
    t = Math.max(0, Math.min(t, c.duration));
    this.reset();
    if (this.withAudio && !fromAudio) Object.values(this.media).forEach(a => { try { a.currentTime = t; } catch {} });
    this.audioT0 = t;
    this.wallT0 = performance.now();
    this.render(t);
  }

  start(withAudio) {
    const c = this.cases[this.i];
    if (this.raf) cancelAnimationFrame(this.raf);
    this.withAudio = !!withAudio;
    if (this.t >= c.duration - .05) this.seek(0);
    this.playing = true;
    this.playBtn.textContent = L.pause;

    this.hint.hidden = true;
    if (this.withAudio) {
      const at = this.t;
      Promise.allSettled(Object.values(this.media).map(a => {
        /* a seek before the metadata has loaded is dropped, so a first click
           on a chapter would start from 0:00: apply it once it can hold */
        const seekTo = () => { try { a.currentTime = at; } catch {} };
        if (a.readyState >= 1) seekTo();
        else a.addEventListener("loadedmetadata", seekTo, { once: true });
        return a.play();
      })).then(rs => {
        /* a muted progress bar is the bug we are avoiding: stop rather than
           keep the clock running against a wall timer with no audio */
        if (refused(rs) && this.playing) {
          this.pause();
          this.hint.textContent = L.blocked;
          this.hint.hidden = false;
        }
      });
    }
    this.audioT0 = this.t;
    this.wallT0 = performance.now();

    this.holding = false;
    this.lastFix = 0;
    const tick = now => {
      if (!this.playing) return;
      const u = this.media.user, a = this.media.assistant;
      let t;
      if (this.withAudio) {
        /* The two tracks are one conversation and arrive over the network at
           their own pace. Until both can play, both wait and so does the scene;
           then they start together. The user track is the clock; a real drift
           pulls the assistant back onto it, at most once a second. */
        if (!bothReady(u, a)) {
          if (!this.holding) this.hold(true);
        } else if (this.holding) {
          this.hold(false);
        } else if (!u.paused && !a.paused && Math.abs(a.currentTime - u.currentTime) > DRIFT && now - this.lastFix > 1000) {
          this.lastFix = now;
          this.ownSeek.add(a);
          try { a.currentTime = u.currentTime; } catch {}
        }
        /* the scene never runs ahead of the sound, nor back; the end of the
           user track is the end of the session, however the last frame rounds */
        t = u.ended ? c.duration : this.holding || u.paused ? this.t : Math.max(this.t, u.currentTime);
      } else {
        t = this.audioT0 + (now - this.wallT0) / 1000;
      }
      this.render(Math.min(t, c.duration));
      if (t >= c.duration) { this.finish(); return; }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  /* both players wait for data together, and start together */
  hold(on) {
    const u = this.media.user, a = this.media.assistant;
    this.holding = on;
    if (on) {
      u.pause(); a.pause();
      this.hint.textContent = L.loading;
      this.hint.hidden = false;
      return;
    }
    this.hint.hidden = true;
    if (Math.abs(a.currentTime - u.currentTime) > .04) { this.ownSeek.add(a); try { a.currentTime = u.currentTime; } catch {} }
    const blocked = e => {
      if (!e || e.name !== "NotAllowedError" || !this.playing) return;
      this.pause(); this.hint.textContent = L.blocked; this.hint.hidden = false;
    };
    u.play().catch(blocked); a.play().catch(blocked);
  }

  pause() {
    if (this.holding) { this.holding = false; this.hint.hidden = true; }
    this.playing = false;
    this.playBtn.textContent = this.t > 0 ? L.replay : L.play;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
    Object.values(this.media || {}).forEach(a => a.pause());
    this.stream.querySelectorAll(".caret").forEach(n => n.remove());
  }

  finish() {
    this.pause();
    this.playBtn.textContent = L.replay;
    this.render(this.cases[this.i].duration);
    this.stream.querySelectorAll(".caret").forEach(n => n.remove());
  }

  stop() {
    this.playing = false;
    this.playBtn.textContent = L.play;
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
    Object.values(this.media || {}).forEach(a => { a.pause(); try { a.currentTime = 0; } catch {} });
  }

  /* the whole session, no animation — what a visitor sees before pressing play */
  showAll() {
    /* a blank, parked scene: the transcript streams in only when played */
    this.reset();
    this.stream.scrollTop = 0;
    this.playBtn.textContent = L.play;
  }
}

/* ---------------------------------------------------------------- mount */
let scene = null;
const mount = document.getElementById("replay");
if (mount && window.DUOPRA_CASES) {
  const s = scene = new Scene(mount, window.DUOPRA_CASES);
  /* one sound at a time: the hero player and the replay stop each other */
  const startScene = s.start.bind(s);
  s.start = withAudio => {
    document.dispatchEvent(new CustomEvent("duopra:play", { detail: "scene" }));
    startScene(withAudio);
  };
  document.addEventListener("duopra:play", e => { if (e.detail !== "scene" && s.playing) s.pause(); });
  const hash = location.hash || "";
  const ci = /(?:^|[#&])case=(\d+)/.exec(hash);
  if (ci) s.load(Math.min(+ci[1], window.DUOPRA_CASES.length - 1));
  const at = /(?:^|[#&])at=([\d.]+)/.exec(hash);
  if (at) {
    s.seek(parseFloat(at[1]));                 /* frozen frame, for checking */
  } else {
    /* Rest until the visitor presses play. A progress bar that moves without
       sound reads as a bug, and browsers refuse to start audio without a
       gesture anyway, so nothing starts on its own. */
    s.showAll();
  }
}

/* ================================================================ the orb
   The deployed app's presence (frontend/static/presence.js), brought over
   unchanged: the same shader in its light theme and the same lean toward
   the pointer. In the app it follows the microphone and the model; here it
   follows the first recording. Only the colours are remapped to this
   page's legend — pink is you, blue is Duopra, teal is an action: your track
   feeds the inward violet–rose flow, Duopra's the outward blue–cyan one, and
   the tool call sends a ripple and one teal satellite round the sphere at the
   second it was sent, until the result is spoken. */
const ORB_VERT = `
attribute vec2 a;
void main() { gl_Position = vec4(a, 0.0, 1.0); }`;

const ORB_FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 uRes;
uniform float uTime, uIn, uOut, uActive, uConnect, uEnd, uKick, uTasks, uLight, uBreath;
uniform vec2 uTilt;

float hash(vec3 p) {
  p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419));
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(mix(hash(i), hash(i + vec3(1.0, 0.0, 0.0)), f.x),
                 mix(hash(i + vec3(0.0, 1.0, 0.0)), hash(i + vec3(1.0, 1.0, 0.0)), f.x), f.y),
             mix(mix(hash(i + vec3(0.0, 0.0, 1.0)), hash(i + vec3(1.0, 0.0, 1.0)), f.x),
                 mix(hash(i + vec3(0.0, 1.0, 1.0)), hash(i + vec3(1.0, 1.0, 1.0)), f.x), f.y), f.z);
}
float fbm(vec3 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 3; i++) {
    v += a * noise(p);
    p = p * 2.02 + vec3(1.7, 9.2, 3.1);
    a *= 0.5;
  }
  return v / 0.875;
}
mat3 rotY(float a) { float c = cos(a), s = sin(a); return mat3(c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c); }
mat3 rotX(float a) { float c = cos(a), s = sin(a); return mat3(1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c); }

void main() {
  float m = min(uRes.x, uRes.y);
  vec2 p = (gl_FragCoord.xy - 0.5 * uRes) / m;
  float t = uTime;
  float energy = max(uIn, uOut);
  float R = 0.27 * (1.0 + uBreath + 0.05 * energy + 0.04 * uKick);
  float d = length(p);
  vec2 dirv = p / max(d, 1e-4);
  float wobA = noise(vec3(dirv * 1.6, t * 0.45)) - 0.5;
  float wobB = noise(vec3(dirv * 3.2 + 4.0, t * 0.9)) - 0.5;
  float rr = R * (1.0 + wobA * (0.03 + 0.09 * uIn) + wobB * (0.02 + 0.07 * uOut));
  float aa = 1.6 / m;
  float inside = 1.0 - smoothstep(rr - aa, rr + aa, d);

  vec3 cyan = vec3(0.33, 0.88, 1.0);
  vec3 blue = vec3(0.15, 0.40, 0.98);
  vec3 violet = vec3(0.56, 0.40, 1.0);
  vec3 magenta = vec3(0.97, 0.38, 0.76);
  vec3 blush = vec3(1.0, 0.80, 0.90);
  vec3 base = mix(vec3(0.02, 0.03, 0.08), vec3(0.88, 0.91, 0.99), uLight);

  vec3 col = vec3(0.0);
  if (inside > 0.0) {
    vec2 q = p / rr;
    float qq = min(dot(q, q), 1.0);
    float z = sqrt(1.0 - qq);
    vec3 N = vec3(q, z);
    vec3 P = rotY(t * 0.11 + uTilt.x) * (rotX(uTilt.y) * N);
    float warp = fbm(P * 1.0 + vec3(0.0, 0.0, t * 0.09));
    float fl = fbm(P * 1.35 + vec3(t * 0.15, -t * 0.08, 0.0) + warp * 1.3);
    float fs = fbm(P * 1.35 + vec3(-t * 0.13, t * 0.11, 3.7) - warp * 1.3);
    float r1 = sqrt(qq);
    float waveL = 0.5 + 0.5 * sin(r1 * 11.0 + t * 4.4 + fl * 8.0);
    float waveS = 0.5 + 0.5 * sin(r1 * 11.0 - t * 4.8 + fs * 8.0);
    float dirL = dot(N, normalize(vec3(-0.55, -0.62, 0.55)));
    float dirS = dot(N, normalize(vec3(0.55, 0.62, 0.55)));
    float L = smoothstep(0.30, 0.74, fl + 0.26 * dirL);
    float S = smoothstep(0.30, 0.74, fs + 0.26 * dirS);
    float baseline = 0.34 + 0.22 * uActive;
    L *= (baseline + 1.1 * uIn) * mix(1.0, 0.4 + 1.2 * waveL, clamp(uIn * 1.6, 0.0, 1.0));
    S *= (baseline + 1.1 * uOut) * mix(1.0, 0.4 + 1.2 * waveS, clamp(uOut * 1.6, 0.0, 1.0));
    vec3 colL = mix(violet, mix(magenta, blush, smoothstep(0.58, 0.78, fl)), smoothstep(0.4, 0.62, fl));
    vec3 colS = mix(blue, cyan, smoothstep(0.35, 0.65, fs));
    vec3 c = base;
    c = mix(c, colL, clamp(L, 0.0, 1.0) * 0.9);
    c = mix(c, colS, clamp(S, 0.0, 1.0) * 0.9);
    float seam = clamp(L * S, 0.0, 1.5) * (0.3 + 1.8 * uIn * uOut);
    c += vec3(1.0, 0.97, 1.0) * seam * mix(0.5, 0.22, uLight);
    float core = exp(-qq * 3.0) * (0.16 + 0.5 * uOut + 0.14 * uIn + 0.08 * uActive);
    c += mix(colL, colS, clamp(0.5 + uOut - uIn, 0.0, 1.0)) * core * mix(0.75, 0.35, uLight);
    float fr = pow(1.0 - z, 2.3);
    float side = smoothstep(-0.6, 0.6, q.x * 0.72 + q.y * 0.68);
    c += mix(mix(violet, magenta, 0.45), cyan, side) * fr * (0.42 + 0.9 * energy) * mix(1.0, 0.55, uLight);
    float spec = pow(max(dot(N, normalize(vec3(-0.42, 0.58, 0.7))), 0.0), 26.0);
    c += vec3(1.0) * spec * mix(0.32, 0.5, uLight);
    float ang = atan(q.y, q.x);
    float sweep = pow(max(cos(ang - t * 2.6), 0.0), 16.0) * (0.25 + fr) * uConnect;
    c += cyan * sweep * 1.6;
    c *= 1.0 - 0.45 * uEnd;
    col = c;
  }

  float e = max(d - rr, 0.0);
  float side2 = smoothstep(-0.5, 0.5, dirv.x * 0.75 + dirv.y * 0.66);
  vec3 haloC = mix(mix(violet, magenta, 0.5), mix(blue, cyan, 0.55), side2);
  float halo = exp(-e * (10.0 - 4.0 * energy)) * (0.16 + 0.45 * energy + 0.08 * uActive) * mix(1.0, 0.7, uLight);
  float ringA = 0.0;
  vec3 ringC = vec3(0.0);
  float lw = 2.8 / m;
  for (int k = 0; k < 2; k++) {
    float ph = fract(t * 0.38 + float(k) * 0.5);
    float rIn = rr * (1.62 - 0.58 * ph);
    float rOut = rr * (1.04 + 0.6 * ph);
    float wIn = (1.0 - smoothstep(0.0, lw, abs(d - rIn))) * sin(ph * 3.14159) * uIn;
    float wOut = (1.0 - smoothstep(0.0, lw, abs(d - rOut))) * (1.0 - ph) * uOut;
    ringA += wIn + wOut;
    ringC += mix(violet, magenta, 0.5) * wIn + mix(blue, cyan, 0.5) * wOut;
  }
  float tapR = rr * (1.02 + 0.6 * (1.0 - uKick));
  float tap = (1.0 - smoothstep(0.0, 3.0 / m, abs(d - tapR))) * uKick * 0.7;
  float satA = 0.0;
  for (int k = 0; k < 5; k++) {
    if (float(k) >= uTasks) break;
    float a2 = t * 0.55 + float(k) * 6.28318 / max(uTasks, 1.0);
    vec2 o = vec2(cos(a2) * rr * 1.45, sin(a2) * rr * 0.42);
    float cr = cos(-0.42), sr = sin(-0.42);
    o = vec2(o.x * cr - o.y * sr, o.x * sr + o.y * cr);
    float hidden = step(0.0, sin(a2)) * inside;
    float dist = length(p - o) * m;
    float g = exp(-dist * 0.17) + 0.28 * exp(-dist * 0.04);
    satA += g * (1.0 - hidden);
  }
  vec3 outer = haloC * halo + ringC + vec3(1.0) * tap;
  float outerA = clamp(halo + ringA + tap, 0.0, 1.0);
  vec3 rgb = col * inside + outer * (1.0 - inside) + vec3(0.16, 0.66, 0.74) * satA;
  float alpha = clamp(inside + outerA * (1.0 - inside) + satA, 0.0, 1.0);
  gl_FragColor = vec4(rgb, alpha);
}`;

/* the call as it is shown under the orb */
const ORB_CALL = "get_exchange_rate(amount: 150, EUR → USD)";

function buildOrb() {
  const fig = document.getElementById("orb");
  if (!fig) return;
  const c = (window.DUOPRA_CASES || [])[0];
  const pk = c && (window.DUOPRA_PEAKS || {})[c.id];
  const stage = fig.querySelector(".orb-stage");
  const canvas = stage && stage.querySelector("canvas");
  const btn = fig.querySelector(".orb-play");
  if (!c || !pk || !canvas || !btn) return;
  const D = c.duration, N = pk.user.length;
  const label = btn.querySelector("span");
  const clock = btn.querySelector("small");
  const stateEl = fig.querySelector(".orb-state");
  const say = fig.querySelector(".orb-say");
  const dots = ["you", "agent", "tool"].map(k => fig.querySelector(".vl." + k));

  /* ---- drawing: WebGL as in the app; without it the CSS pearl stays */
  let gl = null, U = null;
  const initGL = () => {
    let g = null;
    try { g = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: false }); } catch {}
    if (!g) return null;
    const sh = (type, src) => {
      const o = g.createShader(type);
      g.shaderSource(o, src);
      g.compileShader(o);
      return g.getShaderParameter(o, g.COMPILE_STATUS) ? o : null;
    };
    const v = sh(g.VERTEX_SHADER, ORB_VERT), f = sh(g.FRAGMENT_SHADER, ORB_FRAG);
    if (!v || !f) return null;
    const prog = g.createProgram();
    g.attachShader(prog, v);
    g.attachShader(prog, f);
    g.linkProgram(prog);
    if (!g.getProgramParameter(prog, g.LINK_STATUS)) return null;
    g.useProgram(prog);
    g.bindBuffer(g.ARRAY_BUFFER, g.createBuffer());
    /* one triangle that covers the viewport */
    g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), g.STATIC_DRAW);
    const loc = g.getAttribLocation(prog, "a");
    g.enableVertexAttribArray(loc);
    g.vertexAttribPointer(loc, 2, g.FLOAT, false, 0, 0);
    U = Object.fromEntries(["uRes", "uTime", "uIn", "uOut", "uActive", "uConnect", "uEnd", "uKick", "uTasks", "uLight", "uBreath", "uTilt"]
      .map(n => [n, g.getUniformLocation(prog, n)]));
    return g;
  };
  gl = initGL();
  fig.classList.toggle("gl", !!gl);
  canvas.addEventListener("webglcontextlost", e => { e.preventDefault(); gl = null; fig.classList.remove("gl"); });
  canvas.addEventListener("webglcontextrestored", () => { gl = initGL(); fig.classList.toggle("gl", !!gl); wake(); });

  let W = 0;
  const size = () => {
    const r = canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 1.5);
    W = r.width;
    canvas.width = Math.max(1, Math.round(r.width * ratio));
    canvas.height = Math.max(1, Math.round(r.height * ratio));
  };
  size();
  if ("ResizeObserver" in window) new ResizeObserver(size).observe(canvas);

  /* the sphere turns a little toward the pointer: critically damped springs,
     response 0.8 s, the app's values */
  const tiltX = { x: 0, v: 0, goal: 0 }, tiltY = { x: 0, v: 0, goal: 0 };
  const stepSpring = (s, dt) => {
    const w = 2 * Math.PI / .8, k = w * w, f = 2 * w;
    const n = Math.max(1, Math.ceil(dt * 240)), h = dt / n;
    for (let i = 0; i < n; i++) { s.v += (-k * (s.x - s.goal) - f * s.v) * h; s.x += s.v * h; }
  };
  addEventListener("pointermove", e => {
    if (REDUCE.matches || !W || e.pointerType === "touch") return;
    const r = canvas.getBoundingClientRect();
    const nx = (e.clientX - (r.left + r.width / 2)) / (r.width / 2);
    const ny = (e.clientY - (r.top + r.height / 2)) / (r.height / 2);
    tiltY.goal = Math.max(-1, Math.min(1, nx)) * .45;
    tiltX.goal = Math.max(-1, Math.min(1, ny)) * .3;
  }, { passive: true });
  document.addEventListener("pointerleave", () => { tiltX.goal = tiltY.goal = 0; });

  /* ---- the recording: bar heights measured from both tracks */
  const level = (s, t) => {
    const x = t / D * N - .5, i = Math.floor(x), f = x - i;
    const at = j => j < 0 || j >= N ? 0 : Math.max(0, s.charCodeAt(j) - 32) / 94;
    return at(i) * (1 - f) + at(i + 1) * f;
  };
  const segs = [...c.user.map(s => ({ who: "u", s })), ...c.assistant.map(s => ({ who: "a", s }))]
    .sort((p, q) => p.s.t[0] - q.s.t[0]);
  /* an action is in flight from the call until its result is spoken */
  const until = c.calls.map(call => { const nx = c.assistant.find(s => s.t[0] > call.at); return nx ? nx.t[0] : call.at + 3; });
  const inside = (list, t) => list.some(s => t >= s.t[0] && t <= s.t[1]);

  /* the caption: the line being spoken, word by word, and the call under it */
  let lineIdx = -2, lineText = null, lineN = -1, toolShown = 0;
  const caption = t => {
    if (!say) return;
    let idx = -1;
    for (let i = 0; i < segs.length; i++) if (segs[i].s.t[0] <= t) idx = i;
    const fired = c.calls.filter(call => t >= call.at).length;
    if (lineIdx === -2 || fired < toolShown) { say.textContent = ""; toolShown = 0; lineIdx = -1; }
    if (idx !== lineIdx) {
      say.querySelector(".ln.s")?.remove();
      lineIdx = idx; lineN = -1; lineText = null;
      if (idx >= 0) {
        const g = segs[idx];
        const ln = el("span", "ln s " + g.who);
        lineText = document.createTextNode("");
        ln.append(el("span", "who", g.who === "u" ? L.you : L.agent), lineText);
        say.prepend(ln);
      }
    }
    if (lineText) {
      const g = segs[idx], words = pieces(g.s[ZH ? "zh" : "en"]);
      const p = (t - g.s.t[0]) / Math.max(.1, g.s.t[1] - g.s.t[0]);
      const n = Math.min(words.length, Math.max(1, Math.ceil(p * words.length)));
      if (n !== lineN) { lineN = n; lineText.textContent = words.slice(0, n).join(""); }
    }
    if (fired > toolShown) {
      toolShown = fired;
      say.querySelector(".ln.t")?.remove();
      const ln = el("span", "ln t");
      ln.append(el("code", null, ORB_CALL), el("span", "tag", L.never));
      say.append(ln);
    }
  };
  const stateAt = t => {
    const parts = [inside(c.assistant, t) ? L.orbSpeak : L.orbListen];
    if (c.calls.some((call, j) => t >= call.at && t < until[j])) parts.push(L.orbCall);
    return parts.join(L.sep);
  };

  /* ---- playback: the user track is the clock, the other follows it. Over a
     network the two files arrive at their own pace: until both can play, both
     wait (the orb holds, the state says so), then they start together. */
  let au = null, playing = false, ended = false, lastT = 0, started = false, waiting = false, lastFix = 0;
  const media = () => {
    if (!au) {
      au = { u: new Audio(BASE + c.audio.user), a: new Audio(BASE + c.audio.assistant) };
      au.u.preload = au.a.preload = "auto";
      au.u.addEventListener("ended", () => stop(true));
    }
    return au;
  };
  const setState = s => { if (stateEl && stateEl.textContent !== s) stateEl.textContent = s; };
  const setClock = t => { const s = mmss(t); if (clock && clock.textContent !== s) clock.textContent = s; };
  const stop = done => {
    playing = false;
    waiting = false;
    fig.removeAttribute("aria-busy");
    if (au) { au.u.pause(); au.a.pause(); }
    fig.classList.remove("live");
    btn.setAttribute("aria-pressed", "false");
    if (done) {
      ended = true;
      label.textContent = L.orbAgain;
      setState(L.orbEnd);
      setClock(D);
    } else {
      label.textContent = au && au.u.currentTime > 0 ? L.orbResume : L.orbPlay;
      setState(L.orbPaused);
    }
  };
  const start = () => {
    media();
    started = true;
    document.dispatchEvent(new CustomEvent("duopra:play", { detail: "hero" }));
    if (ended || au.u.currentTime >= D - .05) {
      try { au.u.currentTime = 0; au.a.currentTime = 0; } catch {}
      ended = false; lineIdx = -2; lastT = 0;
    }
    playing = true;
    fig.classList.add("live");
    btn.setAttribute("aria-pressed", "true");
    label.textContent = L.orbPause;
    kick = 1;
    waiting = false;
    /* play() inside the click lets both elements sound later (Safari asks for
       the gesture); if they cannot play yet, the frame loop holds them */
    Promise.allSettled([au.u.play(), au.a.play()]).then(rs => {
      /* no sound means no progress: stop rather than run a silent orb */
      if (refused(rs) && playing) stop(false);
    });
    wake();
  };
  const toggle = () => playing ? stop(false) : start();
  btn.addEventListener("click", toggle);
  stage.addEventListener("click", toggle);
  document.addEventListener("duopra:play", e => { if (e.detail !== "hero" && playing) stop(false); });

  /* #orb=19.3 holds the orb at that second of the recording, for checking */
  const held = /(?:^|[#&])orb=([\d.]+)/.exec(location.hash || "");
  const hold = held ? Math.min(D, parseFloat(held[1])) : -1;
  /* fetch both tracks once the page has settled, so the first press can play at once */
  if (hold < 0 && !(navigator.connection && navigator.connection.saveData)) {
    const warm = () => { if (!au) media(); };
    if ("requestIdleCallback" in window) requestIdleCallback(warm, { timeout: 4000 }); else setTimeout(warm, 2000);
  }

  /* ---- the frame loop, only while the orb is on screen */
  let raf = 0, last = 0, onScreen = true, odd = false;
  let voice = 0, speech = 0, active = 0, kick = 0;
  const lit = [0, 0, 0];
  const approach = (v, goal, up, down) => v + (goal - v) * (goal > v ? up : down);
  const frame = ms => {
    raf = 0;
    if (!onScreen || document.hidden) { last = 0; return; }
    raf = requestAnimationFrame(frame);
    const dt = last ? Math.min(1 / 30, Math.max(0, (ms - last) / 1000)) : 1 / 60;
    last = ms;
    const live = playing || (hold >= 0 && !started);
    const t = live && !playing ? hold : au ? au.u.currentTime : 0;
    let flying = 0;
    if (live) {
      if (playing) {
        if (!bothReady(au.u, au.a)) {
          if (!waiting) { waiting = true; au.u.pause(); au.a.pause(); fig.setAttribute("aria-busy", "true"); }
        } else if (waiting) {
          waiting = false; fig.removeAttribute("aria-busy");
          if (Math.abs(au.a.currentTime - au.u.currentTime) > .04) { try { au.a.currentTime = au.u.currentTime; } catch {} }
          /* if the browser now refuses, ask for a press rather than hang */
          const blocked = e => { if (e && e.name === "NotAllowedError" && playing) stop(false); };
          au.u.play().catch(blocked); au.a.play().catch(blocked);
        } else if (!au.a.paused && !au.u.paused && Math.abs(au.a.currentTime - t) > DRIFT && ms - lastFix > 1000) {
          lastFix = ms;
          try { au.a.currentTime = t; } catch {}
        }
        if (c.calls.some(call => lastT < call.at && t >= call.at)) kick = 1;
        lastT = t;
      }
      flying = c.calls.filter((call, j) => t >= call.at && t < until[j]).length;
      caption(t);
      setState(waiting ? L.orbLoading : stateAt(t));
      setClock(t);
      fig.dataset.t = t.toFixed(2);
    }
    /* a live input stream keeps a faint inward flow even in silence */
    /* the bars are peak-normalised, so they are scaled to the levels the app
       sees from a microphone and the model's output */
    voice = approach(voice, live ? Math.min(1, level(pk.user, t) * .62 + .1) : 0, .35, .12);
    speech = approach(speech, live ? Math.min(1, level(pk.assistant, t) * .7) : 0, .35, .1);
    active = approach(active, live ? 1 : 0, .05, .05);
    kick *= .94;
    stepSpring(tiltX, dt);
    stepSpring(tiltY, dt);
    [voice, speech, Math.max(flying, kick)].forEach((v, k) => {
      if (Math.abs(v - lit[k]) < .01) return;
      lit[k] = v;
      if (dots[k]) dots[k].style.setProperty("--l", v.toFixed(2));
    });
    /* at rest the motion is slow: every other frame is enough */
    const resting = !live && kick < .01 && Math.abs(tiltX.v) + Math.abs(tiltY.v) < .002;
    odd = !odd;
    if (resting && odd) return;
    const sec = (ms / 1000 * (REDUCE.matches ? .15 : 1)) % 3600;
    if (gl) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniform2f(U.uRes, canvas.width, canvas.height);
      gl.uniform1f(U.uTime, sec);
      gl.uniform1f(U.uIn, voice);
      gl.uniform1f(U.uOut, speech);
      gl.uniform1f(U.uActive, active);
      gl.uniform1f(U.uConnect, 0);
      gl.uniform1f(U.uEnd, 0);
      gl.uniform1f(U.uKick, REDUCE.matches ? 0 : kick);
      gl.uniform1f(U.uTasks, Math.min(5, flying));
      gl.uniform1f(U.uLight, 1);
      gl.uniform1f(U.uBreath, REDUCE.matches ? 0 : .012 * Math.sin(sec * 1.55));
      gl.uniform2f(U.uTilt, tiltY.x, tiltX.x);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    } else {
      stage.style.setProperty("--e", Math.max(voice, speech).toFixed(3));
    }
  };
  const wake = () => { if (!raf && onScreen && !document.hidden) raf = requestAnimationFrame(frame); };
  if ("IntersectionObserver" in window)
    new IntersectionObserver(es => { onScreen = es[es.length - 1].isIntersecting; wake(); }).observe(stage);
  document.addEventListener("visibilitychange", wake);
  setClock(D);
  wake();
}

/* the small waveform on the "acts mid-sentence" card: both lanes of the
   first recording, with the call marked where it was sent */
function buildMiniLane() {
  const box = document.querySelector(".cap-vis.mini[data-lane]");
  if (!box) return;
  const c = (window.DUOPRA_CASES || [])[+box.dataset.lane];
  const pk = c && (window.DUOPRA_PEAKS || {})[c.id];
  if (!pk || !c.calls.length) return;
  const N = pk.user.length;
  const wrap = el("div", "mlw");
  [["u", pk.user], ["a", pk.assistant]].forEach(([cls, peaks]) => {
    const r = el("div", "ml " + cls);
    r.innerHTML = `<svg viewBox="0 0 ${N} 100" preserveAspectRatio="none"><g fill="currentColor">${barRects(peaks, .6)}</g></svg>`;
    wrap.append(r);
  });
  const call = c.calls[0];
  const mk = el("div", "mk");
  mk.style.left = (call.at / c.duration * 100).toFixed(2) + "%";
  mk.append(el("i"), el("span", null, call.fn + "()"));
  wrap.append(mk);
  box.append(wrap);
}

/* "Hear it" buttons: open the replay at that moment and play it with sound */
function setupHear() {
  document.querySelectorAll(".hear[data-hear]").forEach(b => b.addEventListener("click", () => {
    if (!scene) return;
    const [k, at] = b.dataset.hear.split("@").map(Number);
    scene.load(k);
    scene.seek(at);
    scene.start(true);
    const d = document.getElementById("demo");
    if (d) d.scrollIntoView({ behavior: REDUCE.matches ? "auto" : "smooth", block: "start" });
  }));
}

/* the demo film, first thing under the hero. Set data-src on #film to a
   video file (.mp4 / .webm, a path from the site root such as
   assets/demo.mp4) or to an https embed URL (YouTube /embed/…, Bilibili
   player.bilibili.com/…); data-poster is optional, and text written into
   its <figcaption> becomes the caption. Left empty, the slot shows its
   "coming soon" panel and loads nothing. */
function setupFilm() {
  const fig = document.getElementById("film");
  const src = fig && (fig.dataset.src || "").trim();
  if (!src) return;
  const frame = fig.querySelector(".film-frame");
  const from = u => /^(https?:)?\/\//.test(u) || u.startsWith("/") ? u : BASE + u;
  let node;
  if (/\.(mp4|webm|m4v|mov)([?#].*)?$/i.test(src)) {
    node = document.createElement("video");
    node.controls = true;
    node.playsInline = true;
    node.preload = "metadata";
    if (fig.dataset.poster) node.poster = from(fig.dataset.poster.trim());
    node.src = from(src);
    /* one sound at a time, as with the orb and the replay */
    node.addEventListener("play", () => document.dispatchEvent(new CustomEvent("duopra:play", { detail: "film" })));
    document.addEventListener("duopra:play", e => { if (e.detail !== "film" && !node.paused) node.pause(); });
  } else if (/^https:\/\//.test(src)) {
    node = document.createElement("iframe");
    node.src = src;
    node.loading = "lazy";
    node.allow = "encrypted-media; fullscreen; picture-in-picture";
    node.allowFullscreen = true;
    node.referrerPolicy = "strict-origin-when-cross-origin";
    node.title = fig.dataset.title || "Duopra demo";
  } else return;
  frame.replaceChildren(node);
  fig.classList.add("has-film");
  /* a caption shows only if one was written into the figcaption */
  const cap = fig.querySelector("figcaption");
  if (cap && cap.textContent.trim()) cap.hidden = false;
}

/* charts inside a closed <details> draw when it opens */
function setupDetails() {
  document.querySelectorAll("details").forEach(d => d.addEventListener("toggle", () => {
    if (!d.open) return;
    d.querySelectorAll(".chart:not(.in)").forEach(ch => setTimeout(() => ch.classList.add("in"), 40));
  }));
}

/* ========================================================== reveal & count */
function countUp(n) {
  const to = parseFloat(n.dataset.to), from = parseFloat(n.dataset.from || "0");
  const dec = (n.dataset.to.split(".")[1] || "").length;
  if (REDUCE.matches || !isFinite(to)) { n.textContent = n.dataset.to; return; }
  const dur = 1200, t0 = performance.now();
  const ease = p => 1 - Math.pow(1 - p, 4);
  const step = now => {
    const p = Math.min(1, (now - t0) / dur);
    n.textContent = p >= 1 ? n.dataset.to : (from + (to - from) * ease(p)).toFixed(dec);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function setupReveal() {
  document.querySelectorAll("[data-reveal-group]").forEach(g =>
    [...g.children].forEach((c, i) => c.style.setProperty("--i", i)));
  document.querySelectorAll(".chart").forEach(ch =>
    ch.querySelectorAll(".bt i, .ct, .sb, .pair").forEach((b, i) => b.style.setProperty("--i", i)));
  document.querySelectorAll(".u-row i").forEach((c, k) => c.style.setProperty("--k", k));

  const counts = [...document.querySelectorAll(".count[data-to]")];
  if (!REDUCE.matches) counts.forEach(n => {
    const from = parseFloat(n.dataset.from || "0");
    const dec = (n.dataset.to.split(".")[1] || "").length;
    n.textContent = from.toFixed(dec);
  });

  if (!("IntersectionObserver" in window)) {
    document.querySelectorAll("[data-reveal],[data-reveal-group]").forEach(n => n.classList.add("in"));
    counts.forEach(n => n.textContent = n.dataset.to);
    return;
  }
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add("in");
    io.unobserve(e.target);
  }), { rootMargin: "0px 0px -6% 0px", threshold: .1 });
  document.querySelectorAll("[data-reveal],[data-reveal-group]").forEach(n => io.observe(n));

  const cio = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    cio.unobserve(e.target);
    /* wait for the tile to finish arriving before the figure moves */
    setTimeout(() => countUp(e.target), REDUCE.matches ? 0 : 180);
  }), { threshold: .6 });
  counts.forEach(n => cio.observe(n));
}

/* each word of the statement lights as it reaches the reading line
   (CSS view timelines; without them the text is simply lit) */
function splitStatement() {
  const p = document.querySelector(".statement[data-lit]");
  if (!p || REDUCE.matches || !(window.CSS && CSS.supports("animation-timeline: view()"))) return;
  const walk = node => [...node.childNodes].forEach(n => {
    if (n.nodeType === 1) return walk(n);
    if (n.nodeType !== 3) return;
    const frag = document.createDocumentFragment();
    const parts = ZH ? Array.from(n.textContent) : n.textContent.split(/(\s+)/);
    parts.forEach(s => {
      if (!s) return;
      if (/^\s+$/.test(s)) frag.append(s);
      else frag.append(el("span", "w", s));
    });
    n.replaceWith(frag);
  });
  walk(p);
}

/* ================================================================== nav */
function setupNav() {
  const nav = document.querySelector(".nav");
  if (!nav) return;
  const btn = nav.querySelector(".nav-menu");
  const links = [...nav.querySelectorAll('.nav-links a[href^="#"]')];
  const byKey = new Map(links.map(a => [a.getAttribute("href").slice(1), a]));
  const sections = [...document.querySelectorAll("main section")]
    .map(s => ({ s, key: s.dataset.nav || s.id }))
    .filter(x => byKey.has(x.key) || x.s.id);

  let current = null, ticking = false;
  const update = () => {
    ticking = false;
    nav.dataset.stuck = scrollY > 8 ? "1" : "0";
    const line = innerHeight * .38;
    let key = null;
    for (const { s, key: k } of sections) {
      const r = s.getBoundingClientRect();
      if (r.top <= line && r.bottom > line) { key = byKey.has(k) ? k : null; break; }
    }
    if (key === current) return;
    current = key;
    links.forEach(a => a.toggleAttribute("aria-current", false));
    if (key) byKey.get(key).setAttribute("aria-current", "true");
  };
  addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
  addEventListener("resize", update, { passive: true });
  update();

  if (!btn) return;
  const set = open => {
    nav.dataset.open = open ? "1" : "0";
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", () => set(nav.dataset.open !== "1"));
  links.forEach(a => a.addEventListener("click", () => set(false)));
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && nav.dataset.open === "1") { set(false); btn.focus(); }
  });
  document.addEventListener("pointerdown", e => {
    if (nav.dataset.open === "1" && !nav.contains(e.target)) set(false);
  });
}

/* ============================================================= tooltips
   They enhance, never gate: every value is also printed or in a table. Text
   goes in through textContent; "|" separates the lines, the first is bold. */
function setupTips() {
  const marks = [...document.querySelectorAll("[data-tip]")];
  if (!marks.length) return;
  const tip = el("div", "tip");
  tip.setAttribute("role", "tooltip");
  tip.id = "chart-tip";
  document.body.append(tip);
  let cur = null;
  const place = (m, x) => {
    const r = m.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const cx = x == null ? r.left + r.width / 2 : x;
    const left = Math.max(8, Math.min(cx - tw / 2, innerWidth - tw - 8));
    let top = r.top - th - 10;
    if (top < 60) top = r.bottom + 10;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  };
  const show = (m, x) => {
    tip.textContent = "";
    m.dataset.tip.split("|").forEach((s, i) => tip.append(el(i ? "div" : "b", null, s)));
    cur = m;
    m.setAttribute("aria-describedby", tip.id);
    place(m, x);
    tip.classList.add("on");
  };
  const hide = () => {
    if (cur) cur.removeAttribute("aria-describedby");
    cur = null;
    tip.classList.remove("on");
  };
  marks.forEach(m => {
    if (!m.hasAttribute("aria-label")) m.setAttribute("aria-label", m.dataset.tip.split("|").join(", "));
    m.addEventListener("pointerenter", e => show(m, e.clientX));
    m.addEventListener("pointermove", e => { if (cur === m) place(m, e.clientX); });
    m.addEventListener("pointerleave", hide);
    m.addEventListener("focus", () => show(m));
    m.addEventListener("blur", hide);
  });
  /* a hover tip goes away on scroll; a focused one follows its mark, since
     focusing an off-screen mark scrolls it into view */
  addEventListener("scroll", () => {
    if (!cur) return;
    if (cur === document.activeElement) place(cur); else hide();
  }, { passive: true });
}

/* ========================================================== copy buttons */
function setupCopy() {
  document.querySelectorAll("[data-copy]").forEach(b => {
    b.textContent = L.copy;
    b.addEventListener("click", async () => {
      const code = b.parentElement.querySelector("pre code");
      if (!code) return;
      let ok = true;
      try { await navigator.clipboard.writeText(code.textContent); }
      catch {
        /* no clipboard API (plain http): select the text so ⌘C works */
        const r = document.createRange(); r.selectNodeContents(code);
        const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
        ok = false;
      }
      if (!ok) return;
      b.dataset.done = "1"; b.textContent = L.copied;
      clearTimeout(b._t);
      b._t = setTimeout(() => { b.dataset.done = "0"; b.textContent = L.copy; }, 1600);
    });
  });
}

buildOrb();
setupFilm();
buildMiniLane();
splitStatement();
setupReveal();
setupNav();
setupTips();
setupCopy();
setupHear();
setupDetails();
window.DUOPRA_READY = true;
})();
