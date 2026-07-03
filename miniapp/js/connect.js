import { apiFetch, safeJson } from "./api.js";
import { enterMainApp } from "./mainApp.js";
import { renderOnboardingProgress, markPasswordStepNeeded } from "./progress.js";

// --- Account connect: phone -> code -> (optional) 2FA password ---

export function resetConnectStep() {
  document.getElementById("connect-phone-form").hidden = false;
  document.getElementById("connect-code-form").hidden = true;
  document.getElementById("connect-password-form").hidden = true;
  setActiveStep("phone");
  setConnectStatus("");
}

// Drives both the dot strip and the "Крок N з M" eyebrow text from the same
// shared step list as role-step (see progress.js), so the counter continues
// from where role-step left off (role = step 1) instead of restarting at 1.
function setActiveStep(stepName) {
  renderOnboardingProgress(stepName, "connect-step-eyebrow", ["dot-role", "dot-phone", "dot-code", "dot-password"]);
}

function setConnectStatus(text, variant = "muted") {
  const el = document.getElementById("connect-status");
  el.textContent = text;
  el.classList.toggle("ok", variant === "ok");
  el.classList.toggle("error", variant === "error");
}

// Accepts common ways people actually type a Ukrainian mobile number —
// `+380501234567`, `380501234567`, `0501234567`, or just `501234567` — and
// normalizes all of them to the full `+380...` form the backend/Telethon
// expects, instead of forcing the user to type the country code themselves.
function normalizePhone(raw) {
  const digits = raw.replace(/\D/g, "");
  if (digits.startsWith("380")) return `+${digits}`;
  if (digits.startsWith("0")) return `+380${digits.slice(1)}`;
  if (digits.length === 9) return `+380${digits}`;
  return raw.startsWith("+") ? raw : `+${digits}`;
}

const phoneInput = document.getElementById("phone-input");
const phoneSubmitBtn = document.getElementById("phone-submit-btn");
phoneSubmitBtn.addEventListener("click", async () => {
  const rawPhone = phoneInput.value.trim();
  if (!rawPhone) return;
  const phone = normalizePhone(rawPhone);

  phoneSubmitBtn.disabled = true;
  setConnectStatus("Надсилаю код…");
  const res = await apiFetch("/auth/phone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone }),
  });
  const data = await safeJson(res);

  if (data.status === "code_sent") {
    document.getElementById("connect-phone-form").hidden = true;
    document.getElementById("connect-code-form").hidden = false;
    setActiveStep("code");
    setConnectStatus("Код надіслано в Telegram — введи його нижче.", "ok");
  } else {
    phoneSubmitBtn.disabled = false;
    setConnectStatus(data.error || "Помилка. Спробуй ще раз.", "error");
  }
});

const codeInput = document.getElementById("code-input");
const codeSubmitBtn = document.getElementById("code-submit-btn");
codeSubmitBtn.addEventListener("click", async () => {
  const code = codeInput.value.trim();
  if (!code) return;

  codeSubmitBtn.disabled = true;
  setConnectStatus("Перевіряю код…");
  const res = await apiFetch("/auth/code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  await handleAuthStatus(await safeJson(res), codeSubmitBtn);
});

const passwordInput = document.getElementById("password-input");
const passwordSubmitBtn = document.getElementById("password-submit-btn");
passwordSubmitBtn.addEventListener("click", async () => {
  const password = passwordInput.value;
  if (!password) return;

  passwordSubmitBtn.disabled = true;
  setConnectStatus("Перевіряю пароль…");
  const res = await apiFetch("/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  await handleAuthStatus(await safeJson(res), passwordSubmitBtn);
});

async function handleAuthStatus(data, triggeringButton) {
  if (data.status === "connected") {
    setConnectStatus("Акаунт підключено!", "ok");
    const meRes = await apiFetch("/users/me");
    await enterMainApp(await meRes.json());
    return;
  }

  if (data.status === "password_required") {
    document.getElementById("connect-code-form").hidden = true;
    document.getElementById("connect-password-form").hidden = false;
    markPasswordStepNeeded();
    setActiveStep("password");
    setConnectStatus("На акаунті ввімкнена двофакторна автентифікація — введи пароль.", "ok");
    return;
  }

  triggeringButton.disabled = false;
  setConnectStatus(data.error || "Помилка. Спробуй ще раз.", "error");
}
