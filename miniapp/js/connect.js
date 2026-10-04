import { apiFetch, safeJson } from "./api.js";
import { setupStepForm } from "./step-form.js";
import { fadeIn, fadeOut } from "./transitions.js";
import { goToStep, finishRegistration } from "./onboarding.js";
import { markPasswordStepNeeded } from "./progress.js";

// --- Account connect: QR login -> (optional) 2FA password, as steps 2-3 of
// the unified #role-step stepper (see onboarding.js). Telethon's
// client.qr_login() generates a tg://login token that expires in ~30s; this
// module shows it as a QR image + "Open in Telegram" deep link, then short-
// polls /auth/qr/poll until it's scanned, renewed (token expired, server
// handed back a fresh one), or the attempt errors out. ---

const qrImageEl = document.getElementById("qr-image");
const qrOpenLinkEl = document.getElementById("qr-open-link");
const qrStatusTextEl = document.getElementById("qr-status-text");
const qrErrorEl = document.getElementById("qr-error");

// poll_qr_auth() on the backend already blocks for ~1.5s per call (it's
// wrapping Telethon's own QRLogin.wait(timeout=...)), so this is just the
// gap between one poll's response and firing the next — not the actual
// wait-for-scan interval.
const POLL_GAP_MS = 400;

// Bumped every time QR polling (re)starts — lets an in-flight poll loop
// notice it's been superseded (step re-entered, registration finished some
// other way) and stop touching the DOM instead of needing manual cancellation.
let pollGeneration = 0;

function setQrError(message) {
  if (!qrErrorEl) return;
  if (message) {
    qrErrorEl.textContent = message;
    if (qrErrorEl.hidden) fadeIn(qrErrorEl);
  } else if (!qrErrorEl.hidden) {
    fadeOut(qrErrorEl);
  }
}

function renderQr(data) {
  if (qrImageEl) qrImageEl.src = data.qr_image;
  if (qrOpenLinkEl) qrOpenLinkEl.href = data.qr_url;
}

async function pollLoop(generation) {
  while (generation === pollGeneration) {
    const res = await apiFetch("/auth/qr/poll", { method: "POST" });
    const data = await safeJson(res);

    if (generation !== pollGeneration) return;

    if (data.status === "waiting") {
      await new Promise((resolve) => window.setTimeout(resolve, POLL_GAP_MS));
      continue;
    }

    if (data.status === "qr_renewed") {
      renderQr(data);
      continue;
    }

    if (data.status === "password_required") {
      markPasswordStepNeeded();
      goToStep(3);
      return;
    }

    if (data.status === "connected") {
      finishRegistration();
      return;
    }

    // status === "error"
    setQrError(data.error || "Не вдалося увійти. Спробуй ще раз.");
    if (qrStatusTextEl) qrStatusTextEl.textContent = "Спробуй оновити QR-код.";
    return;
  }
}

// Called from onboarding.js right after the role step completes and the
// stepper moves to step 2 — the QR step has no submit button of its own
// (unlike the old phone/code forms), it starts as soon as it's shown.
export async function startQrLogin() {
  const generation = ++pollGeneration;
  setQrError(null);
  if (qrStatusTextEl) qrStatusTextEl.textContent = "Генерую QR-код…";
  if (qrImageEl) qrImageEl.removeAttribute("src");

  const res = await apiFetch("/auth/qr", { method: "POST" });
  const data = await safeJson(res);

  if (generation !== pollGeneration) return;

  if (data.status !== "qr_pending" || !data.qr_image) {
    setQrError(data.error || "Не вдалося згенерувати QR-код. Спробуй ще раз.");
    return;
  }

  renderQr(data);
  if (qrStatusTextEl) qrStatusTextEl.textContent = "Очікую на сканування…";
  pollLoop(generation);
}

function isNonEmptyPassword(value) {
  return value.length > 0;
}

setupStepForm({
  inputEl: document.getElementById("password-input"),
  submitEl: document.getElementById("password-submit"),
  errorEl: document.getElementById("password-error"),
  isValid: isNonEmptyPassword,
  errorMessage: "Введи пароль",
  busyText: "Перевіряю...",
  submitAction: async (password) => {
    const res = await apiFetch("/auth/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    const data = await safeJson(res);
    if (data.status !== "connected") {
      throw new Error(data.error || "Пароль невірний. Спробуй ще раз.");
    }
  },
  onComplete: () => finishRegistration(),
});
