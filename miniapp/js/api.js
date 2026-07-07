import { tg } from "./telegram.js";

export const API_BASE = window.location.origin;
// Raw, HMAC-signed payload — the backend re-verifies this against the bot
// token on every request. Never trust `initDataUnsafe` for identity: it's
// just a client-side parsed convenience object anyone could forge by
// calling the API directly.
export const initData = tg?.initData ?? "";

// Wraps `fetch` so a network failure (offline, DNS, CORS, WebView killed the
// connection) resolves to a synthetic error Response instead of rejecting.
// Without this, callers that only ever `await res.json()` / check `res.ok`
// would throw uncaught on a dropped connection and the UI would silently
// freeze — the same failure mode `safeJson` already guards against for
// malformed server responses.
export async function apiFetch(path, options = {}) {
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { "X-Telegram-Init-Data": initData, ...options.headers },
    });
  } catch {
    const message = "Немає з'єднання з сервером. Перевір інтернет і спробуй ще раз.";
    return new Response(JSON.stringify({ status: "error", error: message, detail: message }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}

// Parses a JSON body defensively: if the server errored before it could
// produce a proper `{status, error}` payload (e.g. an unhandled exception ->
// bare 500), `res.json()` itself throws on the empty/HTML body. Without this,
// callers would throw uncaught and the UI would silently freeze on
// "Надсилаю код…" forever with no feedback — exactly what happened when
// start_phone_auth could raise unexpected Telethon errors.
export async function safeJson(res) {
  try {
    return await res.json();
  } catch {
    return { status: "error", error: `Сервер повернув помилку (${res.status}). Спробуй ще раз.` };
  }
}
