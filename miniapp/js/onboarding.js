import { apiFetch, initData } from "./api.js";
import { showStep } from "./navigation.js";
import { fadeIn, fadeOut, crossfadeText } from "./transitions.js";
import { buildStepStates, renderStepper, ROLE_STEPPER_LABELS } from "./stepper.js";
import { renderStepHeader } from "./step-header.js";
import { renderRoleList } from "./role-list.js";
import { getTotalSteps, resetPasswordStepNeeded } from "./progress.js";
import { showToast } from "./toast.js";
import { enterMainApp } from "./mainApp.js";

// --- Onboarding: role -> phone -> code -> (optional) 2FA password, all as
// one animated stepper inside #role-step (DESIGN's registration.js layout),
// wired to the real backend instead of DESIGN's `wait()` mock. ---

const roleStepperEl = document.getElementById("role-stepper");
const stepHeaderEl = document.getElementById("step-header");
const stepTitleEl = document.getElementById("step-title");
const stepDescriptionEl = document.getElementById("step-description");
const roleListEl = document.getElementById("role-list");
const stepCards = document.querySelectorAll("#role-step > [data-step]");

// Текст плашки про сесію однаковий для кожного кроку, де вона потрібна
// (позначений data-lock-note) — рендеримо один раз при завантаженні, як і в DESIGN.
const LOCK_NOTE_TEXT =
  "Сесія зберігається у зашифрованому вигляді і використовується лише для дій, які ти сам ініціюєш через бота.";

function renderLockNote(stepPanel) {
  if (!("lockNote" in stepPanel.dataset)) return;
  const note = document.createElement("div");
  note.className = "lock-note";
  note.innerHTML = `<span class="note-text">${LOCK_NOTE_TEXT}</span>`;
  stepPanel.append(note);
}

stepCards.forEach(renderLockNote);

let currentStep = 1;

// Перемальовує степер і заголовок кроку відповідно до currentStep.
// animate=false — для першого показу картки/кроку (на завантаженні чи при
// вході в showRoleStep()), щоб не було зайвого фейду "крок 1 -> потрібний
// крок" перед тим, як секція взагалі стала видимою.
function updateStepUI(animate) {
  const total = getTotalSteps();

  if (roleStepperEl) {
    const states = buildStepStates(total, currentStep);
    renderStepper(roleStepperEl, states, ROLE_STEPPER_LABELS);
  }

  if (stepTitleEl && stepDescriptionEl) {
    if (animate) {
      crossfadeText(stepHeaderEl, () => renderStepHeader(stepTitleEl, stepDescriptionEl, currentStep));
    } else {
      renderStepHeader(stepTitleEl, stepDescriptionEl, currentStep);
    }
  }

  const cards = Array.from(stepCards);
  const targetCard = cards.find((card) => Number(card.dataset.step) === currentStep);
  if (!targetCard) return;

  if (!animate) {
    cards.forEach((card) => {
      card.hidden = card !== targetCard;
    });
    return;
  }

  const visibleCard = cards.find((card) => !card.hidden);
  if (targetCard === visibleCard) return;

  if (visibleCard) {
    fadeOut(visibleCard, () => fadeIn(targetCard));
  } else {
    fadeIn(targetCard);
  }
}

// Єдина точка переходу на інший крок — використовується і роль-кліком тут,
// і connect.js після кожного успішного кроку (телефон/код/пароль).
export function goToStep(step, { animate = true } = {}) {
  currentStep = step;
  updateStepUI(animate);
}

let cachedRoles = null;

// Список посад — той самий для кроку 1 реєстрації і для модалки "Змінити
// посаду" в профілі (profile.js), тож кешуємо один запит замість дублювання
// в обох місцях.
export async function fetchRoles() {
  if (cachedRoles) return cachedRoles;
  const res = await apiFetch("/users/roles");
  cachedRoles = await res.json();
  return cachedRoles;
}

export async function submitRole(roleName) {
  const res = await apiFetch("/users/role", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: roleName }),
  });
  if (!res.ok) throw new Error("Не вдалося зберегти посаду. Спробуй ще раз.");
  return res.json();
}

let roleSelectionBusy = false;

if (roleListEl) {
  // Вибір посади завершує крок 1 — на відміну від DESIGN (локальний стан,
  // без запиту), тут це реальний POST /users/role, тож кнопки на час запиту
  // блокуються, а помилка мережі показується тостом (для степу 1 немає
  // окремого input-error, як для кроків 2-4).
  roleListEl.addEventListener("click", async (event) => {
    const button = event.target.closest(".list-button");
    if (!button || roleSelectionBusy) return;

    roleSelectionBusy = true;
    const buttons = roleListEl.querySelectorAll(".list-button");
    buttons.forEach((btn) => (btn.disabled = true));

    try {
      await submitRole(button.dataset.roleName);
      buttons.forEach((btn) => btn.setAttribute("aria-checked", String(btn === button)));
      goToStep(2);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      roleSelectionBusy = false;
      buttons.forEach((btn) => (btn.disabled = false));
    }
  });
}

// Показує крок 1 (вибір посади) — викликається лише коли реєстрація дійсно
// не завершена (routeByProfile нижче), завжди починаючи з кроку 1: як і в
// оригінальному onboarding.js, реєстрація — один атомарний потік, і
// повторне відкриття Mini App під час незавершеної реєстрації починає її
// заново, а не продовжує з середини.
export async function showRoleStep() {
  resetPasswordStepNeeded();
  const roles = await fetchRoles();
  renderRoleList(roleListEl, roles);
  goToStep(1, { animate: false });
  showStep("role-step");
}

export async function bootstrap() {
  if (!initData) {
    document.getElementById("loading-text").textContent =
      "Відкрий цей застосунок через кнопку меню в чаті з ботом у Telegram.";
    return;
  }

  const res = await apiFetch("/users/me");
  if (!res.ok) {
    document.getElementById("loading-text").textContent =
      "Не вдалося авторизуватись. Спробуй відкрити застосунок ще раз.";
    return;
  }

  const me = await res.json();
  await routeByProfile(me);
}

export async function routeByProfile(me, options = {}) {
  if (!me.role || !me.is_connected) {
    await showRoleStep();
    return;
  }
  await enterMainApp(me, options.initialTab);
}

// Викликається з connect.js, коли /auth/code чи /auth/password повертає
// "connected" — реєстрація завершена, тож просто перезапитуємо актуальний
// профіль і йдемо туди, куди він справді вказує (завжди #main, бо на цей
// момент і роль, і підключення вже готові).
export async function finishRegistration() {
  const meRes = await apiFetch("/users/me");
  const me = await meRes.json();
  await routeByProfile(me, { initialTab: "groups" });
}
