// Ported from DESIGN's transitions.js as an ES module (same behavior,
// verbatim logic) — the fade helpers every other screen-transition in this
// app (navigation.js, tabs.js, toast.js, step-form.js, profile.js, mainApp.js)
// is built on.

// Тривалість fade-переходів — має збігатись зі значенням transition в style.css.
export const FADE_DURATION = 300;

// Плавно ховає елемент: спершу opacity 1 → 0, і лише після завершення
// transition ставить hidden (інакше він зникне миттєво, без анімації).
export function fadeOut(el, onDone) {
  if (!el) {
    if (onDone) onDone();
    return;
  }
  el.classList.add("is-fading");
  window.setTimeout(() => {
    el.hidden = true;
    el.classList.remove("is-fading");
    if (onDone) onDone();
  }, FADE_DURATION);
}

// Плавно показує елемент: знімає hidden, примусово фіксує стартовий кадр
// (opacity: 0), а тоді знімає is-fading — без цього трюку браузер не встигає
// "побачити" стартовий стан і transition просто не відбудеться.
export function fadeIn(el) {
  if (!el) return;
  el.hidden = false;
  el.classList.add("is-fading");
  void el.offsetWidth;
  el.classList.remove("is-fading");
}

// Плавно міняє текст усередині el: спершу тьмяніє, тоді (поки невидимий)
// підмінює вміст через updateFn, і тьмяніє назад. На відміну від fadeOut/fadeIn
// не чіпає hidden/display — елемент лишається в потоці, сусідні блоки не стрибають.
export function crossfadeText(el, updateFn) {
  if (!el) {
    updateFn();
    return;
  }
  el.classList.add("is-fading");
  window.setTimeout(() => {
    updateFn();
    void el.offsetWidth;
    el.classList.remove("is-fading");
  }, FADE_DURATION);
}
