import { t, getLanguage, setLanguage } from "./i18n.js?v=20261027-duopra-r9-personal";
import { Presence } from "./presence.js?v=20261027-duopra-r9-personal";
import { AudioPlayer, encode } from "./audio.js?v=20261027-duopra-r9-personal";
import { MicrophoneCapture } from "./capture.js?v=20261027-duopra-r9-personal";
import { MicrophoneWaveform } from "./waveform.js?v=20261027-duopra-r9-personal";
import { AgencyPanel } from "./agency.js?v=20261027-duopra-r9-personal";
import { Capabilities } from "./capabilities.js?v=20261027-duopra-r9-personal";
import { ToolCalls } from "./toolcalls.js?v=20261027-duopra-r9-personal";
import { pressable } from "./motion.js?v=20261027-duopra-r9-personal";
import { PersonalOrganizer } from "./personal.js?v=20261027-duopra-r9-personal";

const $ = (id) => document.getElementById(id);
const state = {
  mode: "audio",
  phase: "idle",
  connected: false,
  connecting: false,
  stopping: false,
  epoch: 0,
  inputSequence: 0,
  socket: null,
  sessionId: null,
  token: null,
  startedAt: 0,
  configured: null,
  checkingSettings: false,
  file: null,
  fileUrl: null,
  upload: null,
  uploadState: "",
  muted: false,
  outputMuted: false,
  transcript: new Map(),
  online: true,
};
const devices = { microphone: "" };
try {
  const saved = JSON.parse(localStorage.getItem("duplex-devices") || localStorage.getItem("venus-devices") || "{}");
  devices.microphone = saved.microphone || "";
} catch {}
const presence = new Presence($("presence-canvas"));
const microphoneWaveform = new MicrophoneWaveform($("mic-visualizer"));
const agency = new AgencyPanel();
const capabilities = new Capabilities({
  isSessionReady: () => state.connected,
  send: message => { const ok=send(message);if(!ok)notify(t("noTranscript"),true);return ok; }, notify,
  openSettings: () => void openSettings(),
  openBoard: (id) => {
    agency.openTab("capabilities");
    requestAnimationFrame(() => capabilities.focus(id));
  },
});
const toolCalls = new ToolCalls({
  log: (tag, text) => agency.event(tag, text),
  counts: (active, total) => agency.setToolCounts(active, total),
  pulse: () => agency.pulse(),
  open: (key) => {
    agency.openTab("runtime");
    requestAnimationFrame(() => toolCalls.focus(key));
  },
  info: (name) => capabilities.info(name),
  usage: (used) => capabilities.setUsage(used),
  connected: () => capabilities.toolsConnected,
});
const organizer = new PersonalOrganizer({root:$("personal-panel"),permitted:()=>capabilities.personalAuthorized,
  authorize:allowed=>{capabilities.personalAuthorized=allowed;if(state.connected)send({type:"tools_authorize",allowed});capabilities.renderTools();}});
window.addEventListener("duplex-personal-open",()=>agency.openTab("personal"));
const player = new AudioPlayer({
  ack: (message) => send(agency.playbackAck(message)),
  // A short first buffer: here latency is dominated by the model, so waiting
  // to smooth network jitter would only delay the first audible word.
  bufferSeconds: 0.12,
  maxWaitMs: 120,
  onText: appendText,
  onActivity: (active) => {
    if (state.connected) setPhase(active ? "speaking" : "listening");
  },
  onError: () => {
    notify(t("browserAudio"), true);
    void stopSession().catch(() => {});
  },
});
const capture = new MicrophoneCapture({
  onChunk: sendInput,
  onLevel: (level) => (presence.inputLevel = level),
  onEnded: (error) => {
    notify(error ? humanError(error) : t("deviceEnded"), true);
    void stopSession().catch(() => {});
  },
});
let toastTimer;
function notify(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  toastTimer = setTimeout(
    () => ($("toast").hidden = true),
    error ? 7000 : 4200,
  );
}
function humanError(error) {
  const key = {
    NotAllowedError: "permissionDenied",
    PermissionDeniedError: "permissionDenied",
    NotFoundError: "deviceMissing",
    NotReadableError: "deviceBusy",
    InsecureContextError: "insecure",
    AudioBackpressureError: "inputOverloaded",
  }[error?.name];
  return key ? t(key) : error?.message || t("connectionFailed");
}
function setPhase(phase) {
  state.phase = phase;
  document.body.dataset.phase = phase;
  presence.phase = phase;
  agency.setPhase(phase);
  render();
}
function send(message) {
  if (state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify(message));
    return true;
  }
  return false;
}
async function sendInput(pcm) {
  if (!state.connected) return;
  const socket = state.socket, epoch = state.epoch;
  while (socket.bufferedAmount > 256 * 1024) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (!state.connected || state.epoch !== epoch || state.socket !== socket) return;
    if (socket.readyState !== WebSocket.OPEN) throw Error(t("network"));
  }
  if (socket.readyState !== WebSocket.OPEN) throw Error(t("network"));
  socket.send(JSON.stringify({
    type: "audio",
    format: "pcm16",
    sample_rate: 16000,
    data: encode(pcm),
    sequence: state.inputSequence,
  }));
  state.inputSequence++;
  agency.unitSent();
}
function render() {
  const busy = state.connected || state.connecting || state.stopping;
  document.body.dataset.mode = state.mode;
  const micActive = Boolean(capture.stream) && state.mode !== "file" && !state.stopping;
  presence.listening = state.connected && (Boolean(capture.stream) || state.mode === "file");
  microphoneWaveform.setState(micActive, state.muted);
  document.body.classList.toggle("mic-open", micActive);
  for (const button of document.querySelectorAll(".mode-button")) {
    button.classList.toggle("selected", button.dataset.mode === state.mode);
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.mode === state.mode),
    );
    button.disabled = busy || state.checkingSettings || state.configured === null;
  }
  moveModeIndicator();
  $("start-button").hidden = state.connected;
  $("start-button").disabled = state.connecting || state.stopping || state.checkingSettings || state.configured === null;
  $("setup-banner").hidden = busy || state.configured !== false;
  $("start-label").textContent = t(
    state.checkingSettings
      ? "checkingSettings"
      : state.configured === false && !busy
        ? "completeSettings"
        : state.stopping
      ? "ending"
      : state.connecting
        ? "connecting"
        : state.mode === "file"
          ? state.file
            ? "startAudio"
            : "chooseAudio"
          : "start",
  );
  $("stop-button").hidden = !(state.connected || state.connecting);
  $("stop-button").disabled = state.stopping;
  $("mute-mic").hidden = !state.connected || state.mode === "file";
  $("mute-mic").setAttribute("aria-pressed", String(state.muted));
  $("mute-mic")
    .querySelector("use")
    .setAttribute("href", state.muted ? "#i-mic-off" : "#i-mic");
  $("mute-output").hidden = !state.connected;
  $("mute-output").setAttribute("aria-pressed", String(state.outputMuted));
  $("upload-zone").hidden = state.mode !== "file" || Boolean(state.file);
  const preview = state.mode === "file" && Boolean(state.file);
  $("media-preview").hidden = !preview;
  $("experience").classList.toggle("previewing", preview);
  $("replace-audio").hidden = state.mode !== "file" || !state.file || busy;
  $("preview-label").textContent = t(
    state.connected ? "listeningFile" : "audioReady",
  );
  $("preview-audio").controls = state.mode === "file" && !busy;
  const presenceKey = state.connecting
    ? "connecting"
    : state.phase === "speaking"
      ? "speaking"
      : state.connected
        ? state.mode === "file"
          ? "listeningFile"
          : "listening"
        : state.mode === "file"
          ? "audioReady"
          : "presenceIdle";
  $("presence-label").textContent = t(presenceKey);
  let hint = state.mode === "file" ? "audioHint" : "startHint";
  if (state.connected)
    hint =
      state.mode === "file"
        ? state.uploadState === "complete"
          ? "audioFinished"
          : "listeningFile"
        : state.muted
          ? "mutedHint"
          : "liveHint";
  if (state.uploadState === "uploading") hint = "uploading";
  if (state.connecting) hint = "connecting";
  if (state.stopping) hint = "ending";
  if (!busy && state.configured === false) hint = "notConfigured";
  $("control-hint").textContent = t(hint);
  const status = $("connection-state");
  status.classList.toggle("pending", state.connecting || state.stopping);
  status.classList.toggle("offline", !state.online);
  status.lastElementChild.textContent = state.stopping
    ? t("ending")
    : state.connecting
      ? t("connecting")
      : state.connected
        ? `${t("live")} · ${elapsed()}`
        : t(!state.online ? "offline" : state.configured === false ? "setupNeeded" : "available");
  $("reset-button").disabled = state.connecting || state.stopping;
  if (!state.transcript.size)
    $("caption-text").textContent = t(
      state.mode === "file" ? "audioHint" : "prompt",
    );
}
function elapsed() {
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - state.startedAt) / 1000),
  );
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function setMode(mode) {
  if (state.connected || state.connecting || state.stopping) return;
  state.mode = mode;
  state.uploadState = "";
  $("preview-audio").pause();
  if (mode === "file" && state.fileUrl) $("preview-audio").src = state.fileUrl;
  else {
    $("preview-audio").removeAttribute("src");
    $("preview-audio").load();
  }
  $("audio-name").textContent =
    mode === "file" && state.file ? state.file.name : "";
  $("audio-time").textContent = "";
  render();
}
async function refreshStatus(fresh = false) {
  try {
    const response = await fetch(`/api/status${fresh ? "?fresh=true" : ""}`, { cache: "no-store" });
    if (!response.ok) throw Error();
    const status = await response.json();
    state.online = true;
    capabilities.setStatus(status);
    state.configured = status.configured;
    return status;
  } catch {
    state.online = false;
    capabilities.setStatus(null);
    return null;
  } finally {
    render();
  }
}
async function requireSettings() {
  if (state.checkingSettings) return false;
  state.checkingSettings = true;
  render();
  try {
    const status = await refreshStatus(true);
    if (!status) {
      notify(t("network"), true);
      return false;
    }
    if (!status.configured) {
      await openSettings();
      return false;
    }
    return true;
  } finally {
    state.checkingSettings = false;
    render();
  }
}
async function chooseMode(mode) {
  if (state.connected || state.connecting || state.stopping) return;
  if (await requireSettings()) {
    if (mode === "file" || mode === "audio") setMode(mode);
  }
}
async function chooseAudio() {
  if (state.connected || state.connecting || state.stopping) return;
  if (!(await requireSettings())) return;
  if (state.connected || state.connecting || state.stopping) return;
  $("audio-file").click();
}
async function startSession() {
  if (state.connected || state.connecting || state.stopping || state.checkingSettings) return;
  if (state.mode === "file" && !state.file) {
    await chooseAudio();
    return;
  }
  if (state.configured === false) {
    await requireSettings();
    return;
  }
  // Unlock playback during the user's click, before any asynchronous network request.
  const primed = player.prime();
  state.checkingSettings = true;
  const epoch = ++state.epoch;
  render();
  try {
    await primed;
    const status = await refreshStatus(true);
    if (epoch !== state.epoch) return;
    if (!status) throw Error(t("network"));
    if (!status.configured) {
      state.checkingSettings = false;
      render();
      await openSettings();
      return;
    }
    if (status.busy) throw Error(t("busy"));
    await capabilities.refresh();
    if (epoch !== state.epoch) return;
    state.checkingSettings = false;
    state.connecting = true;
    setPhase("connecting");
    agency.clear();
    toolCalls.clear();
    clearTranscript();
    state.muted = false;
    state.uploadState = "";
    if (state.mode !== "file") {
      const started = await capture.start(devices);
      if (!started || epoch !== state.epoch) return;
      render();
      void enumerateDevices();
    }
    if (epoch !== state.epoch) return;
    const ready = await connect(epoch);
    if (epoch !== state.epoch) return;
    state.sessionId = ready.session_id;
    state.token = ready.upload_token;
    state.inputSequence = 0;
    state.connected = true;
    capabilities.renderTools();
    state.connecting = false;
    state.startedAt = Date.now();
    capture.active = true;
    setPhase("listening");
    if (state.mode === "file") await uploadAudio(epoch);
  } catch (error) {
    if (epoch !== state.epoch) return;
    await stopSession().catch(() => {});
    if (error.code === "configuration") {
      state.configured = false;
      await openSettings();
    } else notify(humanError(error), true);
  } finally {
    state.checkingSettings = false;
    render();
  }
}
async function connect(epoch) {
  await organizer.ready;
  return new Promise((resolve, reject) => {
    const source = state.mode === "file" ? "audio_file" : "live";
    const url = new URL(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    url.searchParams.set("mode", "audio");
    url.searchParams.set("source", source);
    const initialTools = capabilities.sessionTools();
    if (initialTools !== null) url.searchParams.set("initial_tools", JSON.stringify(initialTools));
    url.searchParams.set("personal_authorized",String(capabilities.personalAuthorized));
    const socket = new WebSocket(url.href);
    state.socket = socket;
    let settled = false;
    let serverRejected = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(Error(t("connectionFailed")));
        socket.close();
      }
    }, 180000);
    socket.onmessage = (event) => {
      if (epoch !== state.epoch) return;
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      capabilities.handle(message);
      organizer.syncAuthorization();
      if(message.type==="tool_event"&&message.event?.type==="completed"&&/^(calendar|task)\./.test(message.event.tool||""))void organizer.refresh();
      if (message.type === "ready") {
        capture.firstUnitSamples = Number(message.native_first_samples) || 16560;
        agency.setSession(message);
        toolCalls.setSession(message);
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          resolve(message);
        }
        return;
      }
      if (message.type === "fatal_error") {
        serverRejected = true;
        const error = Error(
          message.code === "busy"
            ? t("busy")
            : message.code === "configuration"
              ? t("notConfigured")
              : message.error || t("connectionFailed"),
        );
        error.code = message.code;
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(error);
        } else if (error.code === "configuration") {
          state.configured = false;
          void stopSession().then(() => openSettings()).catch(() => {});
        } else {
          notify(humanError(error), true);
          void stopSession().catch(() => {});
        }
        return;
      }
      if (toolCalls.handle(message) || agency.handle(message)) return;
      if (message.type === "playback") {
        agency.playbackReceived(message);
        player.enqueue(message);
      } else if (message.type === "playback_cancel") {
        player.cancel(message.utterance_ids);
        for (const key of agency.receipts.keys())
          if (message.utterance_ids.some((id) => key.startsWith(`${id}:`))) agency.receipts.delete(key);
      } else if (message.type === "playback_end") player.finish(message.utterance_id);
      else if (message.type === "text")
        appendText(message.text, message.utterance_id);
      else if (message.type === "media_status") audioStatus(message);
      else if (message.type === "command_error") notify(message.error, true);
    };
    socket.onerror = () => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(Error(t("network")));
      }
    };
    socket.onclose = () => {
      agency.endSession();
      clearTimeout(timer);
      if (!settled) {
        settled = true;
        reject(Error(t("network")));
      }
      if (epoch === state.epoch && !state.stopping && !serverRejected) {
        notify(t("network"), true);
        void stopSession(false).catch(() => {});
      }
    };
  });
}
async function stopSession(notifyServer = true) {
  if (state.stopping) return state.stopPromise;
  state.stopping = true;
  state.connecting = false;
  state.connected = false;
  capabilities.renderTools();
  state.epoch++;
  setPhase("ending");
  state.upload?.abort();
  state.upload = null;
  player.clear();
  capture.active = false;
  const socket = state.socket;
  state.stopPromise = (async () => {
    const stopped = capture.stop();
    try {
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => {
            socket.close();
            reject(Error(t("sessionClosing")));
          }, 45000);
          socket.addEventListener(
            "close",
            () => {
              clearTimeout(timer);
              resolve();
            },
            { once: true },
          );
          if (notifyServer && socket.readyState === WebSocket.OPEN)
            socket.send(JSON.stringify({ type: "stop_session" }));
          else socket.close();
        });
      }
      await stopped;
    } finally {
      state.socket = null;
      state.sessionId = null;
      state.token = null;
      state.stopping = false;
      state.uploadState = "";
      $("upload-progress").hidden = true;
      $("preview-audio").pause();
      agency.end();
      toolCalls.end();
      setPhase("idle");
      void refreshStatus();
    }
  })();
  try {
    await state.stopPromise;
  } catch (error) {
    notify(humanError(error), true);
    throw error;
  } finally {
    state.stopPromise = null;
  }
}
async function selectFile(file) {
  if (!file || state.connected || state.connecting || state.stopping) return;
  if (file.size > 10 * 1024 * 1024) {
    notify(t("fileTooLarge"), true);
    return;
  }
  const validFile = file.type.startsWith("audio/") || /\.(wav|mp3|m4a|flac|ogg|opus|aac|aiff|aif|wma)$/i.test(file.name);
  if (!validFile) {
    notify(t("invalidAudio"), true);
    return;
  }
  if (!(await requireSettings())) return;
  if (state.connected || state.connecting || state.stopping) return;
  if (state.fileUrl) URL.revokeObjectURL(state.fileUrl);
  state.file = file;
  state.fileUrl = URL.createObjectURL(file);
  setMode("file");
}
async function uploadAudio(epoch) {
  state.uploadState = "uploading";
  $("upload-progress").hidden = false;
  const bar = $("upload-progress").querySelector("span");
  bar.style.width = "0%";
  render();
  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.upload = xhr;
    xhr.open(
      "POST",
      `/api/sessions/${encodeURIComponent(state.sessionId)}/audio`,
    );
    xhr.setRequestHeader("X-Venus-Session-Token", state.token);
    xhr.setRequestHeader(
      "Content-Type",
      state.file.type || "application/octet-stream",
    );
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        bar.style.width = `${Math.round((event.loaded / event.total) * 100)}%`;
    };
    xhr.onload = () => {
      state.upload = null;
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        let detail;
        try {
          detail = JSON.parse(xhr.responseText).detail;
        } catch {}
        reject(Error(detail || t("audioFailed")));
      }
    };
    xhr.onerror = () => reject(Error(t("network")));
    xhr.onabort = () =>
      reject(Object.assign(Error("Upload cancelled"), { name: "AbortError" }));
    xhr.send(state.file);
  });
  if (epoch !== state.epoch) return;
  state.uploadState = "feeding";
  $("upload-progress").hidden = true;
  const audio = $("preview-audio");
  audio.currentTime = 0;
  await audio.play().catch(() => {});
  render();
}
function audioStatus(message) {
  state.uploadState = message.state;
  if (message.seconds !== undefined)
    $("audio-time").textContent = `${message.seconds}s`;
  if (message.state === "feeding") {
    const audio = $("preview-audio");
    if (
      Number.isFinite(audio.duration) &&
      Math.abs(audio.currentTime - message.seconds) > 2
    )
      audio.currentTime = Math.min(audio.duration, message.seconds);
  }
  if (message.state === "complete") $("preview-audio").pause();
  if (message.state === "error") {
    notify(t("audioFailed"), true);
    $("preview-audio").pause();
  }
  render();
}
function clearTranscript() {
  state.transcript.clear();
  $("transcript").replaceChildren();
  const empty = document.createElement("p");
  empty.className = "empty-copy";
  empty.dataset.i18n = "historyEmpty";
  empty.textContent = t("historyEmpty");
  $("transcript").append(empty);
  $("caption").classList.remove("has-speech");
  $("caption-speaker").hidden = true;
  $("caption-text").textContent = t("prompt");
}
function appendText(text, id) {
  if (!text) return;
  const key = id || `text-${state.transcript.size}`;
  let entry = state.transcript.get(key);
  if (!entry) {
    $("transcript").querySelector(".empty-copy")?.remove();
    const node = document.createElement("article");
    node.className = "transcript-entry";
    const heading = document.createElement("header");
    heading.textContent = "DUOPRA";
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString(getLanguage(), {
      hour: "2-digit",
      minute: "2-digit",
    });
    heading.append(time);
    const paragraph = document.createElement("p");
    node.append(heading, paragraph);
    $("transcript").append(node);
    entry = { text: "", node, paragraph, time: Date.now() };
    state.transcript.set(key, entry);
  }
  entry.text += text;
  entry.paragraph.textContent = entry.text;
  $("caption").classList.add("has-speech");
  $("caption-speaker").hidden = false;
  $("caption-text").textContent = entry.text;
  $("caption-text").scrollTop = $("caption-text").scrollHeight;
  $("transcript").scrollTop = $("transcript").scrollHeight;
}
function saveBlob(blob, name) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
async function enumerateDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const available = await navigator.mediaDevices.enumerateDevices();
    const select = $("microphone-device");
    select.replaceChildren(new Option(t("defaultDevice"), ""));
    for (const [i, device] of available.filter(value => value.kind === "audioinput").entries())
      select.add(new Option(device.label || `${t("microphone")} ${i + 1}`, device.deviceId));
    select.value = devices.microphone;
  } catch {}
}
async function openSettings() {
  await enumerateDevices();
  const busy = state.connected || state.connecting || state.stopping;
  for (const node of $("settings-form").querySelectorAll("select,button")) node.disabled = busy;
  $("settings-message").textContent = busy ? t("settingsActive") : "";
  if (!$("settings-dialog").open) $("settings-dialog").showModal();
}
$("settings-form").onsubmit = (event) => {
  event.preventDefault();
  if (state.connected || state.connecting || state.stopping) return;
  devices.microphone = $("microphone-device").value;
  try { localStorage.setItem("duplex-devices", JSON.stringify(devices)); } catch {}
  $("settings-dialog").close();
  notify(t("settingsSaved"));
};
for (const button of document.querySelectorAll(".mode-button"))
  button.onclick = () => void chooseMode(button.dataset.mode);
$("start-button").onclick = () => void startSession();
$("stop-button").onclick = () => void stopSession().catch(() => {});
$("mute-mic").onclick = () => {
  state.muted = !state.muted;
  capture.setMuted(state.muted);
  agency.event("in", t(state.muted ? "micMuted" : "yourVoice"));
  render();
};
$("mute-output").onclick = () => {
  state.outputMuted = !state.outputMuted;
  player.setMuted(state.outputMuted);
  render();
};
$("audio-file").onchange = (event) => void selectFile(event.target.files[0]);
$("replace-audio").onclick = () => void chooseAudio();
$("upload-zone").onclick = (event) => {
  event.preventDefault();
  void chooseAudio();
};
$("upload-zone").onkeydown = (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    void chooseAudio();
  }
};
for (const name of ["dragenter", "dragover"])
  $("upload-zone").addEventListener(name, (event) => {
    event.preventDefault();
    $("upload-zone").classList.add("dragging");
  });
for (const name of ["dragleave", "drop"])
  $("upload-zone").addEventListener(name, (event) => {
    event.preventDefault();
    $("upload-zone").classList.remove("dragging");
    if (name === "drop") void selectFile(event.dataTransfer.files[0]);
  });
$("settings-button").onclick = () => void openSettings();
$("complete-settings").onclick = () => void openSettings();
$("history-button").onclick = () => $("history-dialog").showModal();
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.querySelector(".dialog-close").onclick = () => dialog.close();
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    if (
      event.target === dialog &&
      (event.clientX < rect.left ||
        event.clientX > rect.right ||
        event.clientY < rect.top ||
        event.clientY > rect.bottom)
    )
      dialog.close();
  });
}
$("reset-button").onclick = async () => {
  const restart = state.connected;
  $("history-dialog").close();
  try {
    await player.prime();
    await stopSession();
    agency.clear();
    toolCalls.clear();
    clearTranscript();
    if (restart) await startSession();
    else notify(t("resetDone"));
  } catch {}
};
$("save-transcript").onclick = () => {
  if (!state.transcript.size) {
    notify(t("noTranscript"));
    return;
  }
  saveBlob(
    new Blob(
      [
        [...state.transcript.values()]
          .map(
            (entry) =>
              `Duopra · ${new Date(entry.time).toLocaleString()}\n${entry.text}`,
          )
          .join("\n\n"),
      ],
      { type: "text/plain;charset=utf-8" },
    ),
    "Duopra-conversation.txt",
  );
};
$("language-button").onclick = () =>
  setLanguage(getLanguage() === "en" ? "zh" : "en");
function renderTheme() {
  const label = t(document.documentElement.dataset.theme === "dark" ? "lightTheme" : "darkTheme");
  $("theme-button").setAttribute("aria-label", label);
  $("theme-button").title = label;
}
window.addEventListener("duplex-theme", renderTheme);
window.addEventListener("duplex-language", () => {
  renderTheme();
  render();
  void enumerateDevices();
});
window.addEventListener("pagehide", () => {
  state.epoch++;
  player.clear();
  state.upload?.abort();
  capture.stop();
  if (state.socket?.readyState === WebSocket.OPEN)
    send({ type: "stop_session" });
  state.socket?.close();
});
navigator.mediaDevices?.addEventListener?.(
  "devicechange",
  () => void enumerateDevices(),
);
setInterval(() => {
  if (state.connected) render();
}, 1000);
setInterval(() => {
  if (!state.connected && !state.connecting && !state.stopping)
    void refreshStatus();
}, 15000);
function animate(time) {
  presence.outputLevel = player.amplitude();
  presence.tasks = toolCalls.activeCount;
  agency.setQueue(player);
  if (microphoneWaveform.active && !document.hidden)
    microphoneWaveform.draw(capture.readWaveform(), time);
  requestAnimationFrame(animate);
}
/** The mode switch's indicator glides to the selected button on a spring. */
function moveModeIndicator() {
  const indicator = document.querySelector(".mode-indicator");
  const selected = document.querySelector(".mode-button.selected:not([hidden])");
  if (!indicator || !selected) return;
  const first = !indicator.classList.contains("ready");
  if (first) indicator.style.transition = "none";
  indicator.style.transform = `translateX(${selected.offsetLeft}px)`;
  indicator.style.width = `${selected.offsetWidth}px`;
  if (first) {
    void indicator.offsetWidth;
    indicator.style.transition = "";
    indicator.classList.add("ready");
  }
}
for (const button of document.querySelectorAll(".pressable")) pressable(button);
window.addEventListener("resize", moveModeIndicator);
document.fonts?.ready.then(moveModeIndicator);
// Live objects for visual checks from the console; nothing here fabricates data.
window.duplexDebug = { state, presence, agency, capabilities, toolCalls, player, organizer, setPhase };
requestAnimationFrame(animate);
renderTheme();
render();
void refreshStatus();
