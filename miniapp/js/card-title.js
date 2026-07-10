// Ported from DESIGN's card-title.js as an ES module — still runs as a
// side effect at import time (module scripts execute after DOM parsing,
// same timing `defer` gave the original), so every element already in the
// initial HTML with a [data-title] attribute gets its header injected once.

// Заголовок картки (крапка + h2) однаковий для кожного етапу — генеруємо його
// один раз із data-title, а не дублюємо розмітку в HTML. Ставимо саме в
// .card-container (візуальну картку), бо в межах етапу можуть бути ще й інші
// блоки поза карткою (наприклад .lock-note) — заголовок їх не стосується.
export function renderCardTitle(stepPanel) {
  const title = stepPanel.dataset.title;
  if (!title) return;

  const card = stepPanel.querySelector(".card-container") ?? stepPanel;

  const titleEl = document.createElement("div");
  titleEl.className = "title";
  titleEl.innerHTML = `<span class="dot" aria-hidden="true"></span><h2>${title}</h2>`;
  card.prepend(titleEl);
}

// Заголовок потрібен не лише крокам реєстрації, а будь-якій картці з
// data-title (наприклад, картці "Створити групу" в #main) — тож рендеримо
// його для всіх таких елементів, а не тільки для stepCards.
document.querySelectorAll("[data-title]").forEach(renderCardTitle);
