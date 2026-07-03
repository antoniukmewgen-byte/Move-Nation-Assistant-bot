// Telegram WebApp SDK bootstrap — must run once, as early as possible,
// before anything else touches `tg` or reads `initData`.
export const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
// Best-effort branding — older Telegram clients may not support these calls,
// so we guard each one instead of letting an unsupported call break bootstrap.
try {
  tg?.setBackgroundColor?.("#f6f7fb");
  tg?.setHeaderColor?.("#6d5bff");
} catch {
  // Unsupported Telegram client version — the app still works with defaults.
}
