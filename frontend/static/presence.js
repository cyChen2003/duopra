/** The duplex core: a fluid sphere of light carrying two voices at once. Cyan is
 * you (the input stream, flowing inward); violet is Duopra (the output stream,
 * radiating outward). When both are live they braid together and light gathers
 * where they meet. Driven by real input/output levels; it is not model state. */
import { Spring } from "./motion.js?v=20261027-duopra-r9-personal";

const VERTEX = `
attribute vec2 a;
void main() { gl_Position = vec4(a, 0.0, 1.0); }`;

const FRAGMENT = `
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
  // The silhouette breathes with both voices.
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
    // Listening flows inward; speaking radiates outward.
    float waveL = 0.5 + 0.5 * sin(r1 * 11.0 + t * 4.4 + fl * 8.0);
    float waveS = 0.5 + 0.5 * sin(r1 * 11.0 - t * 4.8 + fs * 8.0);
    float dirL = dot(N, normalize(vec3(-0.55, -0.62, 0.55)));
    float dirS = dot(N, normalize(vec3(0.55, 0.62, 0.55)));
    float L = smoothstep(0.30, 0.74, fl + 0.26 * dirL);
    float S = smoothstep(0.30, 0.74, fs + 0.26 * dirS);
    float baseline = 0.34 + 0.22 * uActive;
    L *= (baseline + 1.1 * uIn) * mix(1.0, 0.4 + 1.2 * waveL, clamp(uIn * 1.6, 0.0, 1.0));
    S *= (baseline + 1.1 * uOut) * mix(1.0, 0.4 + 1.2 * waveS, clamp(uOut * 1.6, 0.0, 1.0));
    vec3 colL = mix(blue, cyan, smoothstep(0.35, 0.65, fl));
    vec3 colS = mix(violet, mix(magenta, blush, smoothstep(0.58, 0.78, fs)), smoothstep(0.4, 0.62, fs));
    vec3 c = base;
    c = mix(c, colL, clamp(L, 0.0, 1.0) * 0.9);
    c = mix(c, colS, clamp(S, 0.0, 1.0) * 0.9);
    // Where both voices overlap, light gathers: the full-duplex seam.
    float seam = clamp(L * S, 0.0, 1.5) * (0.3 + 1.8 * uIn * uOut);
    c += vec3(1.0, 0.97, 1.0) * seam * mix(0.5, 0.22, uLight);
    float core = exp(-qq * 3.0) * (0.16 + 0.5 * uOut + 0.14 * uIn + 0.08 * uActive);
    c += mix(colL, colS, clamp(0.5 + uOut - uIn, 0.0, 1.0)) * core * mix(0.75, 0.35, uLight);
    float fr = pow(1.0 - z, 2.3);
    float side = smoothstep(-0.6, 0.6, q.x * 0.72 + q.y * 0.68);
    c += mix(cyan, mix(violet, magenta, 0.45), side) * fr * (0.42 + 0.9 * energy) * mix(1.0, 0.55, uLight);
    float spec = pow(max(dot(N, normalize(vec3(-0.42, 0.58, 0.7))), 0.0), 26.0);
    c += vec3(1.0) * spec * mix(0.32, 0.5, uLight);
    float ang = atan(q.y, q.x);
    float sweep = pow(max(cos(ang - t * 2.6), 0.0), 16.0) * (0.25 + fr) * uConnect;
    c += cyan * sweep * 1.6;
    c *= 1.0 - 0.45 * uEnd;
    col = c;
  }

  // Outside: a halo tinted by whichever voice is on that side, rings that fall
  // inward while you talk and rise outward while it talks, a tap ripple, and one
  // satellite per action in flight.
  float e = max(d - rr, 0.0);
  float side2 = smoothstep(-0.5, 0.5, dirv.x * 0.75 + dirv.y * 0.66);
  vec3 haloC = mix(mix(blue, cyan, 0.55), mix(violet, magenta, 0.5), side2);
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
    ringC += cyan * wIn + mix(violet, magenta, 0.5) * wOut;
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
    float g = exp(-dist * 0.35) + 0.25 * exp(-dist * 0.06);
    satA += g * (1.0 - hidden);
  }
  vec3 outer = haloC * halo + ringC + vec3(1.0) * tap;
  float outerA = clamp(halo + ringA + tap, 0.0, 1.0);
  vec3 rgb = col * inside + outer * (1.0 - inside) + vec3(0.95, 0.9, 1.0) * satA;
  float alpha = clamp(inside + outerA * (1.0 - inside) + satA, 0.0, 1.0);
  gl_FragColor = vec4(rgb, alpha);
}`;

export class Presence {
  constructor(canvas) {
    this.canvas = canvas;
    this.inputLevel = 0;
    this.outputLevel = 0;
    this.phase = "idle";
    this.tasks = 0;
    this.listening = false; // an input stream (microphone or uploaded media) is live
    this.voice = 0;
    this.speech = 0;
    this.activity = 0;
    this.connect = 0;
    this.ending = 0;
    this.kick = 0;
    this.visible = true;
    this.legend = document.querySelector(".voice-legend");
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)");
    this.tiltX = new Spring({ damping: 1, response: 0.8, threshold: 0.0004 });
    this.tiltY = new Spring({ damping: 1, response: 0.8, threshold: 0.0004 });
    this.light = document.documentElement.dataset.theme === "light";
    window.addEventListener("duplex-theme", () => {
      this.light = document.documentElement.dataset.theme === "light";
    });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.attach(canvas);
    if (!this.initGL()) this.fallback();
    document.addEventListener("visibilitychange", () => {
      this.visible = !document.hidden;
    });
    window.addEventListener("pointermove", (event) => this.lean(event), { passive: true });
    document.addEventListener("pointerleave", () => {
      this.tiltX.target = 0;
      this.tiltY.target = 0;
    });
    requestAnimationFrame((time) => this.draw(time));
  }
  attach(canvas) {
    this.canvas = canvas;
    this.resizeObserver.disconnect();
    this.resizeObserver.observe(canvas);
    canvas.addEventListener("pointerdown", () => {
      this.kick = 1;
    });
    this.resize();
  }
  initGL() {
    const gl = this.canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: false,
      powerPreference: "high-performance",
    });
    if (!gl) return false;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.warn("Core shader failed to compile", gl.getShaderInfoLog(shader));
        return null;
      }
      return shader;
    };
    const vertex = compile(gl.VERTEX_SHADER, VERTEX),
      fragment = compile(gl.FRAGMENT_SHADER, FRAGMENT);
    if (!vertex || !fragment) return false;
    const program = gl.createProgram();
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      console.warn("Core shader failed to link", gl.getProgramInfoLog(program));
      return false;
    }
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    // One triangle that covers the whole viewport.
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "a");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    this.uniforms = Object.fromEntries(
      ["uRes", "uTime", "uIn", "uOut", "uActive", "uConnect", "uEnd", "uKick", "uTasks", "uLight", "uBreath", "uTilt"].map(
        (name) => [name, gl.getUniformLocation(program, name)],
      ),
    );
    this.gl = gl;
    this.canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.gl = null;
    });
    this.canvas.addEventListener("webglcontextrestored", () => {
      if (!this.initGL()) this.fallback();
    });
    this.resize();
    return true;
  }
  /** Without WebGL, a quieter two-tone gradient sphere stands in. */
  fallback() {
    this.gl = null;
    const fresh = this.canvas.cloneNode(false);
    this.canvas.replaceWith(fresh);
    this.attach(fresh);
    this.context = fresh.getContext("2d");
  }
  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, this.gl ? 1.5 : 2);
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.max(1, Math.round(rect.width * ratio));
    this.canvas.height = Math.max(1, Math.round(rect.height * ratio));
    if (this.context) this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }
  /** The sphere turns a little toward the pointer; springs keep it calm. */
  lean(event) {
    if (this.reduced.matches || !this.width) return;
    const rect = this.canvas.getBoundingClientRect();
    const nx = (event.clientX - (rect.left + rect.width / 2)) / (rect.width / 2);
    const ny = (event.clientY - (rect.top + rect.height / 2)) / (rect.height / 2);
    this.tiltY.target = Math.max(-1, Math.min(1, nx)) * 0.45;
    this.tiltX.target = Math.max(-1, Math.min(1, ny)) * 0.3;
  }
  draw(time) {
    requestAnimationFrame((next) => this.draw(next));
    if (!this.visible || !this.width || !this.height) return;
    const still = this.reduced.matches;
    const live = this.phase === "listening" || this.phase === "speaking";
    const approach = (value, target, rise, fall) => value + (target - value) * (target > value ? rise : fall);
    this.activity = approach(this.activity, live ? 1 : this.phase === "connecting" ? 0.6 : 0, 0.05, 0.05);
    this.connect = approach(this.connect, this.phase === "connecting" ? 1 : 0, 0.08, 0.06);
    this.ending = approach(this.ending, this.phase === "ending" ? 1 : 0, 0.08, 0.06);
    // A live input stream keeps a faint inward flow even in silence.
    const heard = Math.min(1, this.inputLevel * 1.2 + (live && this.listening ? 0.16 : 0));
    this.voice = approach(this.voice, heard, 0.35, 0.12);
    this.speech = approach(this.speech, Math.min(1, this.outputLevel * 1.4), 0.35, 0.1);
    this.kick *= 0.94;
    const seconds = ((time / 1000) * (still ? 0.15 : 1)) % 3600;
    this.legend?.style.setProperty("--you", this.voice.toFixed(3));
    this.legend?.style.setProperty("--agent", this.speech.toFixed(3));
    if (this.gl) this.drawGL(seconds);
    else if (this.context) this.draw2D(seconds);
  }
  drawGL(seconds) {
    const gl = this.gl,
      u = this.uniforms;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(u.uRes, this.canvas.width, this.canvas.height);
    gl.uniform1f(u.uTime, seconds);
    gl.uniform1f(u.uIn, this.voice);
    gl.uniform1f(u.uOut, this.speech);
    gl.uniform1f(u.uActive, this.activity);
    gl.uniform1f(u.uConnect, this.connect);
    gl.uniform1f(u.uEnd, this.ending);
    gl.uniform1f(u.uKick, this.kick);
    gl.uniform1f(u.uTasks, Math.min(5, this.tasks));
    gl.uniform1f(u.uLight, this.light ? 1 : 0);
    gl.uniform1f(u.uBreath, this.reduced.matches ? 0 : 0.012 * Math.sin(seconds * 1.55));
    gl.uniform2f(u.uTilt, this.tiltY.value, this.tiltX.value);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }
  draw2D(seconds) {
    const ctx = this.context,
      w = this.width,
      h = this.height,
      r = Math.min(w, h) * 0.27 * (1 + 0.05 * Math.max(this.voice, this.speech));
    const cx = w / 2,
      cy = h / 2;
    ctx.clearRect(0, 0, w, h);
    const sphere = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.35, r * 0.1, cx, cy, r);
    sphere.addColorStop(0, `rgba(157,140,255,${0.55 + this.speech * 0.4})`);
    sphere.addColorStop(0.55, `rgba(38,102,250,${0.5 + this.voice * 0.3})`);
    sphere.addColorStop(1, `rgba(84,224,255,${0.6 + this.voice * 0.4})`);
    ctx.fillStyle = sphere;
    ctx.beginPath();
    ctx.arc(cx, cy, r * (1 + 0.01 * Math.sin(seconds * 1.55)), 0, Math.PI * 2);
    ctx.fill();
  }
}
