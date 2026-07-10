// Ported from DESIGN's ripple.js as an ES module — self-registers a single
// delegated pointerdown listener as a side effect of being imported (see
// main.js). No exports needed; nothing else in the app calls createRipple
// directly, same as in DESIGN.

// Тривалість анімації — має збігатись зі значенням animation в style.css
// (.ripple, 0.6s). Тримаємо тут окремою константою, а не хардкоджений
// таймаут нижче, — той самий підхід, що й FADE_DURATION в transitions.js.
const RIPPLE_DURATION = 600;

// Material-style ripple: хвиля стартує саме з точки кліку і росте до розміру,
// що гарантовано покриває найдальший кут кнопки (2x більший бік — простий і
// надійний запас, без точних тригонометричних розрахунків).
function createRipple(event, button) {
  const existingRipple = button.querySelector(".ripple");
  if (existingRipple) {
    existingRipple.remove();
  }

  const rect = button.getBoundingClientRect();
  const diameter = Math.max(rect.width, rect.height) * 2;
  const radius = diameter / 2;

  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.width = `${diameter}px`;
  ripple.style.height = `${diameter}px`;
  ripple.style.left = `${event.clientX - rect.left - radius}px`;
  ripple.style.top = `${event.clientY - rect.top - radius}px`;

  button.appendChild(ripple);
  // setTimeout замість animationend: клік по кнопці часто одразу запускає
  // fadeOut екрана (назад, видалення групи тощо), а FADE_DURATION (300ms)
  // коротший за RIPPLE_DURATION (600ms) — display:none від [hidden] встигає
  // спрацювати раніше й скасовує CSS-анімацію без події animationend. Ріпл
  // лишався б "застряглим" у DOM і, при поверненні кнопки на екран, анімація
  // стартувала б заново сама по собі. setTimeout прибирає його завжди, навіть
  // якщо кнопку сховали на середині анімації.
  window.setTimeout(() => ripple.remove(), RIPPLE_DURATION);
}

// Делегування на document: працює для всіх кнопок одразу, включно з
// тими, що рендер-функції (renderRoleList, renderGroupList, renderMemberList)
// ще тільки згенерують чи перегенерують у майбутньому — не треба вішати
// обробник на кожну кнопку окремо.
// button.icon-list-button і button.list-button (тег+клас, а не просто клас) —
// щоб зловити лише справжні кнопки, а не візуально схожі, але неклікабельні
// елементи з тим самим класом: декоративні <span>-бейджі іконок
// (icon-list-button, зокрема й аватарки-ініціали в member-list.js).
document.addEventListener("pointerdown", (event) => {
  const button = event.target.closest(
    ".primary, .danger, .tab-btn, button.icon-list-button, button.list-button, .member-delete, .link-button"
  );
  if (button) {
    createRipple(event, button);
  }
});
