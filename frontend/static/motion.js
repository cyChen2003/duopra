/** The motion layer. Springs start from the current on-screen value, carry velocity
 * across re-targets, and can be grabbed mid-flight; gestures hand their release
 * velocity to the spring so dragging and animating form one continuous motion. */
const reduced = matchMedia("(prefers-reduced-motion: reduce)");

// One display-synced clock steps every active spring.
const active = new Set();
let frame = 0;
let last = 0;
function loop(now) {
  frame = 0;
  const dt = last ? Math.min(1 / 30, Math.max(0, (now - last) / 1000)) : 1 / 60;
  last = now;
  for (const spring of active) spring.step(dt);
  if (active.size) frame = requestAnimationFrame(loop);
  else last = 0;
}
function schedule() {
  if (!frame) frame = requestAnimationFrame(loop);
}

/** Parameters follow Apple's designer-facing pair: damping ratio (1 = settles
 * without overshoot) and response (approximate duration in seconds). */
export class Spring {
  constructor({
    damping = 1,
    response = 0.4,
    value = 0,
    threshold = 0.05,
    onUpdate,
    onSettle,
  } = {}) {
    Object.assign(this, { damping, response, value, threshold, onUpdate, onSettle });
    this.goal = value;
    this.velocity = 0;
  }
  get target() {
    return this.goal;
  }
  set target(goal) {
    this.goal = goal;
    if (reduced.matches) {
      this.snap(goal);
      return;
    }
    active.add(this);
    schedule();
  }
  snap(value) {
    active.delete(this);
    this.goal = value;
    this.value = value;
    this.velocity = 0;
    this.onUpdate?.(value);
    this.onSettle?.(value);
  }
  /** Hold the current value, e.g. when a finger grabs a moving sheet. */
  freeze() {
    active.delete(this);
    this.velocity = 0;
  }
  /** Continue at the gesture's release velocity instead of starting from rest. */
  kick(velocity) {
    if (reduced.matches) return;
    this.velocity = velocity;
    active.add(this);
    schedule();
  }
  step(dt) {
    const omega = (2 * Math.PI) / this.response,
      stiffness = omega * omega,
      friction = 2 * this.damping * omega;
    const steps = Math.max(1, Math.ceil(dt * 240)),
      h = dt / steps;
    for (let i = 0; i < steps; i++) {
      this.velocity += (-stiffness * (this.value - this.goal) - friction * this.velocity) * h;
      this.value += this.velocity * h;
    }
    if (
      Math.abs(this.velocity) < this.threshold * 10 &&
      Math.abs(this.value - this.goal) < this.threshold
    ) {
      active.delete(this);
      this.value = this.goal;
      this.velocity = 0;
      this.onUpdate?.(this.value);
      this.onSettle?.(this.value);
      return;
    }
    this.onUpdate?.(this.value);
  }
}

/** Where a flick would come to rest under scroll-style deceleration (px/s in). */
export function project(velocity, decelerationRate = 0.998) {
  return ((velocity / 1000) * decelerationRate) / (1 - decelerationRate);
}

/** Progressive resistance past a boundary, so an edge reads as soft, not frozen. */
export function rubberband(overshoot, dimension, constant = 0.55) {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot));
}

/** The same curves style.css uses, for Web Animations started from script. */
export const EASE = {
  bouncy:
    "linear(0, 0.01522 2.1%, 0.05516 4.2%, 0.1124 6.2%, 0.1808 8.3%, 0.2556 10.4%, 0.3331 12.5%, 0.4102 14.6%, 0.4848 16.7%, 0.5554 18.8%, 0.621 20.8%, 0.6809 22.9%, 0.7348 25.0%, 0.7828 27.1%, 0.8248 29.2%, 0.8613 31.2%, 0.8925 33.3%, 0.9189 35.4%, 0.941 37.5%, 0.9591 39.6%, 0.9738 41.7%, 0.9856 43.8%, 0.9948 45.8%, 1.002 47.9%, 1.007 50.0%, 1.011 52.1%, 1.013 54.2%, 1.014 56.2%, 1.015 58.3%, 1.015 60.4%, 1.015 62.5%, 1.014 64.6%, 1.013 66.7%, 1.012 68.8%, 1.011 70.8%, 1.01 72.9%, 1.008 75.0%, 1.007 77.1%, 1.006 79.2%, 1.005 81.2%, 1.004 83.3%, 1.004 85.4%, 1.003 87.5%, 1.002 89.6%, 1.002 91.7%, 1.001 93.8%, 1.001 95.8%, 1.001 97.9%, 1)",
  soft: "cubic-bezier(0.2, 0.8, 0.2, 1)",
};

/** Feedback on pointer-down, not on release; the release springs back with a
 * little overshoot because the press itself carried the energy. */
export function pressable(element, { scale = 0.96 } = {}) {
  let pressed = false;
  const release = () => {
    if (!pressed) return;
    pressed = false;
    element.classList.remove("is-pressed");
    element.style.transform = "";
    if (reduced.matches) return;
    element.animate(
      [{ transform: `scale(${scale})` }, { transform: "scale(1)" }],
      { duration: 560, easing: EASE.bouncy },
    );
  };
  element.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || element.disabled) return;
    pressed = true;
    element.classList.add("is-pressed");
    element.style.transform = `scale(${scale})`;
  });
  for (const type of ["pointerup", "pointercancel", "pointerleave"])
    element.addEventListener(type, release);
}

/** A panel that slides along one axis. Closed is a positive offset in px; open is 0.
 * The handle tracks the pointer 1:1 (respecting the grab point), rubber-bands past
 * open, and on release projects momentum to decide where to settle. */
export class Sheet {
  constructor(element, { handle, axis, closedOffset, onProgress, onOpenChange }) {
    Object.assign(this, { element, axis, closedOffset, onProgress, onOpenChange });
    this.handle = handle || element;
    this.open = false;
    this.dragging = false;
    this.spring = new Spring({
      damping: 0.82,
      response: 0.34,
      threshold: 0.1,
      onUpdate: (value) => this.apply(value),
      onSettle: () => this.settle(),
    });
    this.spring.snap(this.closedOffset());
    this.element.style.visibility = "hidden";
    this.handle.addEventListener("pointerdown", (event) => this.down(event));
    this.handle.addEventListener("pointermove", (event) => this.move(event));
    for (const type of ["pointerup", "pointercancel"])
      this.handle.addEventListener(type, () => this.up());
    window.addEventListener("resize", () => this.relayout());
  }
  relayout() {
    this.spring.snap(this.open ? 0 : this.closedOffset());
  }
  position(event) {
    return this.axis() === "x" ? event.clientX : event.clientY;
  }
  apply(value) {
    const closed = this.closedOffset();
    const progress = Math.max(0, Math.min(1, 1 - value / closed));
    this.element.style.transform =
      this.axis() === "x"
        ? `translate3d(${value}px, 0, 0)`
        : `translate3d(0, ${value}px, 0)`;
    this.element.style.setProperty("--sheet-progress", progress.toFixed(3));
    // The stylesheet hides the sheet until script runs, so visibility is set explicitly.
    if (value < closed) this.element.style.visibility = "visible";
    this.onProgress?.(progress);
  }
  settle() {
    if (!this.open && !this.dragging) this.element.style.visibility = "hidden";
  }
  setOpen(open, velocity = 0) {
    const changed = open !== this.open;
    this.open = open;
    this.element.style.visibility = "visible";
    this.element.inert = !open;
    this.element.setAttribute("aria-hidden", String(!open));
    this.spring.target = open ? 0 : this.closedOffset();
    if (velocity) this.spring.kick(velocity);
    if (changed) this.onOpenChange?.(open);
  }
  down(event) {
    if (event.button !== 0) return;
    if (event.target.closest("button, a, input, select, textarea, summary")) return;
    this.handle.setPointerCapture(event.pointerId);
    this.dragging = true;
    this.spring.freeze(); // grab it where it is, even mid-flight
    this.start = this.position(event);
    this.startValue = this.spring.value;
    this.history = [[performance.now(), this.start]];
    this.element.classList.add("dragging");
  }
  move(event) {
    if (!this.dragging) return;
    const position = this.position(event);
    this.history.push([performance.now(), position]);
    if (this.history.length > 6) this.history.shift();
    let value = this.startValue + (position - this.start);
    if (value < 0) {
      const size = this.axis() === "x" ? this.element.offsetWidth : this.element.offsetHeight;
      value = -rubberband(-value, size);
    }
    this.spring.value = value;
    this.apply(value);
  }
  up() {
    if (!this.dragging) return;
    this.dragging = false;
    this.element.classList.remove("dragging");
    let velocity = 0;
    const [first, latest] = [this.history[0], this.history[this.history.length - 1]];
    if (latest[0] > first[0]) velocity = ((latest[1] - first[1]) / (latest[0] - first[0])) * 1000;
    const closed = this.closedOffset();
    const projected = this.spring.value + project(velocity);
    // A decisive flick wins by its direction; otherwise the projected resting point decides.
    const shouldClose = Math.abs(velocity) > 320 ? velocity > 0 : projected > closed * 0.4;
    this.setOpen(!shouldClose, velocity);
  }
}
