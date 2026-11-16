/** Ordered PCM16 input units with the native model's first-chunk size. */
import { resample, pcm16 } from "./audio.js?v=20261027-duopra-r9-personal";
export class MicrophoneCapture {
  constructor({ onChunk, onLevel, onEnded }) {
    Object.assign(this, { onChunk, onLevel, onEnded });
    this.generation = 0;
    this.stream = null;
    this.context = null;
    this.node = null;
    this.analyser = null;
    this.waveform = null;
    this.pending = new Float32Array(0);
    this.chain = Promise.resolve();
    this.queued = 0;
    this.muted = false;
    this.active = false;
    this.firstUnitSamples = 16560;
    this.firstUnit = true;
  }
  async start(devices) {
    this.firstUnit = true;
    if (!navigator.mediaDevices?.getUserMedia)
      throw Object.assign(Error("Secure context required"), {
        name: "InsecureContextError",
      });
    const generation = ++this.generation;
    const constraints = (withIds) => ({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        ...(withIds && devices.microphone
          ? { deviceId: { exact: devices.microphone } }
          : {}),
      },
      video: false,
    });
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints(true));
    } catch (error) {
      // A saved device id that no longer exists raises OverconstrainedError;
      // retry once with the system defaults instead of failing the session.
      const overconstrained =
        error?.name === "OverconstrainedError" || /constraint/i.test(error?.message || "");
      if (!overconstrained || !devices.microphone) throw error;
      console.warn("Saved device id is no longer available; using defaults", error);
      stream = await navigator.mediaDevices.getUserMedia(constraints(false));
    }
    if (generation !== this.generation) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    this.stream = stream;
    this.muted = false;
    for (const track of stream.getTracks())
      track.onended = () => {
        if (this.stream === stream) this.onEnded();
      };
    const Context = window.AudioContext || window.webkitAudioContext;
    const context = new Context();
    this.context = context;
    await context.resume();
    await context.audioWorklet.addModule("/static/capture-worklet.js?v=20261027-duopra-r9-personal");
    if (generation !== this.generation) {
      await context.close().catch(() => {});
      return false;
    }
    const source = context.createMediaStreamSource(stream),
      analyser = context.createAnalyser(),
      node = new AudioWorkletNode(context, "duplex-capture"),
      gain = context.createGain();
    analyser.fftSize = 2048;
    this.analyser = analyser;
    this.waveform = new Float32Array(analyser.fftSize);
    gain.gain.value = 0;
    source.connect(analyser).connect(node).connect(gain).connect(context.destination);
    this.node = node;
    node.port.onmessage = ({ data }) => {
      if (generation !== this.generation || !(data instanceof Float32Array))
        return;
      let energy = 0;
      for (const value of data) energy += value * value;
      this.onLevel(this.muted ? 0 : Math.min(1, Math.sqrt(energy / data.length) * 7));
      if (!this.active) {
        this.firstUnit = true;
        this.pending = new Float32Array(0);
        return;
      }
      const pending = new Float32Array(this.pending.length + data.length);
      pending.set(this.pending);
      pending.set(data, this.pending.length);
      this.pending = pending;
      while (true) {
        const size = Math.round(context.sampleRate * (this.firstUnit ? this.firstUnitSamples / 16000 : 1));
        if (this.pending.length < size) break;
        const frame = this.pending.slice(0, size);
        this.pending = this.pending.slice(size);
        this.firstUnit = false;
        this.queue(pcm16(resample(frame, context.sampleRate)), generation);
      }
    };
    return true;
  }
  queue(pcm, generation) {
    if (this.queued >= 10) {
      this.active = false;
      this.onEnded(Object.assign(Error("Audio input queue is full"), { name: "AudioBackpressureError" }));
      return;
    }
    this.queued++;
    this.chain = this.chain
      .then(() => {
        if (generation !== this.generation || !this.active) return;
        return this.onChunk(pcm);
      })
      .catch((error) => {
        if (generation === this.generation) this.onEnded(error);
      })
      .finally(() => {
        if (generation === this.generation) this.queued--;
      });
  }
  setMuted(value) {
    this.muted = value;
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = !value));
    if (value) this.onLevel(0);
  }
  readWaveform() {
    if (!this.analyser || !this.context || this.muted) return null;
    this.analyser.getFloatTimeDomainData(this.waveform);
    return { samples: this.waveform, sampleRate: this.context.sampleRate };
  }
  async stop() {
    this.firstUnit = true;
    this.generation++;
    this.active = false;
    this.chain = Promise.resolve();
    this.queued = 0;
    this.node?.disconnect();
    this.node = null;
    this.analyser?.disconnect();
    this.analyser = null;
    this.waveform = null;
    this.stream?.getTracks().forEach((track) => {
      track.onended = null;
      track.stop();
    });
    this.stream = null;
    const context = this.context;
    this.context = null;
    if (context) await context.close().catch(() => {});
    this.pending = new Float32Array(0);
    this.onLevel(0);
  }
}
