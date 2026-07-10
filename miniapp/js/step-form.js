import { fadeIn, fadeOut } from "./transitions.js";

// Ported from DESIGN's step-form.js as an ES module. The mock `wait()` helper
// is gone — submitAction is now a real `apiFetch(...)` call. One small,
// deliberate extension over the original: submitAction's resolved value is
// now passed into onComplete(result). DESIGN never needed this (its
// submitAction was just `wait(ms)`, resolving to undefined), but the real
// backend responses carry data the caller needs afterwards (e.g. connect.js's
// code step needs to know whether the server said "connected" or
// "password_required" to decide which step to go to next). Existing-style
// onComplete callbacks that ignore the argument keep working unchanged.

export function setupStepForm({ inputEl, submitEl, errorEl, isValid, errorMessage, busyText, submitAction, onComplete }) {
  if (!submitEl) return;

  const idleText = submitEl.textContent;

  function setError(message) {
    if (!errorEl) return;

    if (message) {
      errorEl.textContent = message;
      if (errorEl.hidden) fadeIn(errorEl);
    } else if (!errorEl.hidden) {
      fadeOut(errorEl);
    }
  }

  submitEl.addEventListener("click", async () => {
    const value = inputEl ? inputEl.value : "";

    if (!isValid(value)) {
      // errorMessage може бути як фіксованим текстом, так і функцією від
      // значення — коли причина невалідності буває різна.
      const message = typeof errorMessage === "function" ? errorMessage(value) : errorMessage;
      setError(message);
      inputEl?.focus();
      return;
    }

    setError(null);
    submitEl.disabled = true;
    submitEl.textContent = busyText;

    try {
      const result = await submitAction(value);
      onComplete(result);
    } catch (error) {
      // Помилка від сервера (наприклад, "код прострочився") — на відміну від
      // isValid, ця перевірка відбувається вже після відправки на бекенд, тож
      // трапляється вже після того, як клієнтська валідація пройшла успішно.
      setError(error instanceof Error ? error.message : String(error));
      inputEl?.focus();
    } finally {
      submitEl.disabled = false;
      submitEl.textContent = idleText;
    }
  });

  if (inputEl) {
    // Ховаємо помилку одразу, як тільки почав виправляти значення — не чекаємо
    // повторного сабміту, щоб не дратувати текстом помилки під час вводу.
    inputEl.addEventListener("input", () => setError(null));

    // Enter у полі — природна дія "підтвердити", як на клавіатурі з autofill.
    inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") submitEl.click();
    });
  }
}
