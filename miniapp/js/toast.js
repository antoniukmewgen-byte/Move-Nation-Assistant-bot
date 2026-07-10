import { fadeIn, fadeOut } from "./transitions.js";

// Ported from DESIGN's toast.js as an ES module.

const toastEl = document.getElementById("toast");
const toastTextEl = document.getElementById("toast-text");

// Скільки toast лишається видимим, перш ніж сам сховається — окрема
// константа, а не хардкод у showToast, той самий підхід, що й FADE_DURATION
// в transitions.js і RIPPLE_DURATION в ripple.js.
const TOAST_DURATION = 2200;

// Таймер поточного показу — зберігаємо, щоб швидка друга дія (наприклад,
// видалив одного клієнта одразу за іншим) скасовувала таймер першого toast,
// а не ховала другий передчасно чужим таймером.
let toastTimer = null;

// Короткий фідбек про дію — викликається з onboarding.js/profile.js/mainApp.js
// після дій, після яких на екрані не завжди й одразу видно, що щось відбулось.
// variant "error" фарбує іконку в --danger (див. .toast.error i в style.css) —
// для помилок мережі/бекенду, а не лише для happy path, як у DESIGN.
export function showToast(message, variant = "ok") {
  if (!toastEl || !toastTextEl) return;

  toastTextEl.textContent = message;
  toastEl.classList.toggle("error", variant === "error");

  if (toastTimer) window.clearTimeout(toastTimer);
  // fadeIn лише якщо toast ще не видно — інакше is-fading скине й одразу
  // зніме опаціті-transition, і вже показаний toast непотрібно моргне.
  if (toastEl.hidden) fadeIn(toastEl);

  toastTimer = window.setTimeout(() => {
    fadeOut(toastEl);
    toastTimer = null;
  }, TOAST_DURATION);
}
