// Ported from DESIGN's stepper.js as an ES module — pure DOM helpers, no
// behavior changes. Drives the onboarding progress dots in onboarding.js.

// Підписи кроків для aria-label — без цього скрінрідер бачить лише "порожній елемент списку".
export const ROLE_STEPPER_LABELS = ["Посада", "Телефон", "Код", "Пароль"];

// Рахує стан кожного кроку відносно current:
// усе до нього — "done", сам він — "active", решта — "upcoming".
export function buildStepStates(total, current) {
  return Array.from({ length: total }, (_, i) => {
    const stepNumber = i + 1;
    if (stepNumber < current) return "done";
    if (stepNumber === current) return "active";
    return "upcoming";
  });
}

const STATE_SUFFIX = {
  active: ", поточний крок",
  done: ", завершено",
};

// Виставляє state/aria/іконку на вже існуючому (або щойно створеному) .step-вузлі.
// Винесено окремо, бо викликається і при першому рендері, і при оновленні на місці.
function applyStepState(step, state, index, labels) {
  const label = labels[index] ?? `Крок ${index + 1}`;
  const suffix = STATE_SUFFIX[state] ?? "";
  step.setAttribute("aria-label", `Крок ${index + 1}: ${label}${suffix}`);

  if (state === "upcoming") {
    delete step.dataset.state;
  } else {
    step.dataset.state = state;
  }

  if (state === "active") {
    step.setAttribute("aria-current", "step");
  } else {
    step.removeAttribute("aria-current");
  }

  const needsIcon = state === "done";
  const existingIcon = step.querySelector("i");

  if (needsIcon && !existingIcon) {
    const icon = document.createElement("i");
    icon.className = "fa-solid fa-check step-icon";
    icon.setAttribute("aria-hidden", "true");
    step.appendChild(icon);
  } else if (!needsIcon && existingIcon) {
    existingIcon.remove();
  }
}

function applyLineState(line, state) {
  if (state === "done") {
    line.dataset.state = "done";
  } else {
    delete line.dataset.state;
  }
}

export function renderStepper(container, states, labels = []) {
  const existingSteps = container.querySelectorAll(".step");

  // Якщо кількість кроків не змінилась — оновлюємо існуючі DOM-вузли на місці:
  // так CSS-transition бачить зміну властивості на тому самому елементі й анімує її.
  // Якщо кроків стало більше/менше (наприклад, з'явився крок 2FA) — простіше перемалювати з нуля.
  if (existingSteps.length === states.length) {
    existingSteps.forEach((step, index) => {
      applyStepState(step, states[index], index, labels);
    });

    container.querySelectorAll(".step-line").forEach((line, index) => {
      applyLineState(line, states[index]);
    });

    return;
  }

  container.innerHTML = "";

  states.forEach((state, index) => {
    const step = document.createElement("div");
    step.className = "step center";
    step.setAttribute("role", "listitem");
    applyStepState(step, state, index, labels);
    container.appendChild(step);

    const isLastStep = index === states.length - 1;
    if (!isLastStep) {
      const line = document.createElement("div");
      line.className = "step-line";
      line.setAttribute("aria-hidden", "true");
      applyLineState(line, state);
      container.appendChild(line);
    }
  });
}
