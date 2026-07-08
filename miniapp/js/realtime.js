import { initData } from "./api.js";

// Persistent per-user WebSocket for real-time invalidation pushes — opened
// once (see mainApp.js::enterMainApp) and kept open for the whole time the
// Mini App is in the foreground, not per-screen: reopening per screen would
// mean connection-lifecycle races on fast tab switching (a close from the
// screen you just left racing the open from the screen you just entered).
// The server only ever sends small "X changed" signals (see
// app/services/realtime.py) — this module's only job is to keep the socket
// alive across drops and hand each parsed event to whatever handler the
// caller registered for its `type`. It deliberately knows nothing about
// *what* a "groups_changed" event should trigger — that's mainApp.js's job,
// via the `handlers` map passed to `connectRealtime`.

const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 30000;

let socket = null;
let reconnectDelay = RECONNECT_BASE_DELAY_MS;
let handlers = {};

export function connectRealtime(eventHandlers) {
  handlers = eventHandlers;
  open();
}

function open() {
  if (!initData) return;

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

  socket.addEventListener("open", () => {
    reconnectDelay = RECONNECT_BASE_DELAY_MS;
    // initData is sent as the first message, not a query param/header — see
    // app/api/routes/realtime.py's module docstring for why.
    socket.send(initData);
  });

  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }
    const handler = handlers[data.type];
    if (handler) handler(data);
  });

  // A dropped connection (backgrounded WebView, flaky network, server
  // restart) is entirely normal — reconnect with backoff rather than giving
  // up; there is no user-facing error state for "realtime is momentarily
  // down", the Mini App just silently falls back to its last known state
  // until the next successful push (or the user's own actions, which still
  // refetch directly regardless of this socket).
  socket.addEventListener("close", scheduleReconnect);
  socket.addEventListener("error", () => socket.close());
}

function scheduleReconnect() {
  setTimeout(open, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_DELAY_MS);
}
