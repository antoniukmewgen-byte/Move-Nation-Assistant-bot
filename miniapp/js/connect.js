import { apiFetch, safeJson } from "./api.js";
import { setupStepForm } from "./step-form.js";
import { goToStep, finishRegistration } from "./onboarding.js";
import { markPasswordStepNeeded } from "./progress.js";

// --- Account connect: phone -> code -> (optional) 2FA password, as steps
// 2-4 of the unified #role-step stepper (see onboarding.js). Each form uses
// DESIGN's setupStepForm (busy state + input-error display), with
// submitAction doing the real request instead of DESIGN's wait(ms) mock. ---

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

function isNonEmptyPhone(value) {
  return value.trim().length > 0;
}

setupStepForm({
  inputEl: document.getElementById("phone-input"),
  submitEl: document.getElementById("phone-submit"),
  errorEl: document.getElementById("phone-error"),
  isValid: isNonEmptyPhone,
  errorMessage: "Введи номер телефону",
  busyText: "Надсилаю код...",
  submitAction: async (rawPhone) => {
    const phone = normalizePhone(rawPhone.trim());
    const res = await apiFetch("/auth/phone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
    });
    const data = await safeJson(res);
    if (data.status !== "code_sent") {
      throw new Error(data.error || "Не вдалося надіслати код. Спробуй ще раз.");
    }
  },
  onComplete: () => goToStep(3),
});

function isNonEmptyCode(value) {
  return value.trim().length > 0;
}

setupStepForm({
  inputEl: document.getElementById("code-input"),
  submitEl: document.getElementById("code-submit"),
  errorEl: document.getElementById("code-error"),
  isValid: isNonEmptyCode,
  errorMessage: "Введи код із Telegram",
  busyText: "Підтверджую...",
  submitAction: async (code) => {
    const res = await apiFetch("/auth/code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code.trim() }),
    });
    const data = await safeJson(res);
    if (data.status === "connected" || data.status === "password_required") {
      return data.status;
    }
    throw new Error(data.error || "Код невірний. Спробуй ще раз.");
  },
  onComplete: (status) => {
    if (status === "password_required") {
      markPasswordStepNeeded();
      goToStep(4);
    } else {
      finishRegistration();
    }
  },
});

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
