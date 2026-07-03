// --- Shared onboarding progress: role -> phone -> code -> (password) ---
//
// Single source of truth for step numbering across the *whole* registration
// flow, not just within one screen — so the "Крок N з M" text never appears
// to reset back to 1 when moving from picking a role to connecting the
// account. Both screens render their own eyebrow + dot strip (they're
// separate DOM sections, only one visible at a time), but both call
// renderOnboardingProgress() so they always agree on the same count.
export const ONBOARDING_STEPS = ["role", "phone", "code", "password"];

// Whether the password (2FA) step actually exists is only known once the
// user has submitted their SMS code — Telegram doesn't expose "does this
// account have 2FA enabled?" ahead of that exchange (see connect.js's
// `password_required` handling). Until then we assume the common case (no
// 2FA -> 3 total steps) instead of always counting a step most accounts will
// never see.
let passwordStepNeeded = false;

export function markPasswordStepNeeded() {
  passwordStepNeeded = true;
}

export function renderOnboardingProgress(activeStep, eyebrowId, dotIds) {
  const total = passwordStepNeeded ? ONBOARDING_STEPS.length : ONBOARDING_STEPS.length - 1;
  const stepNumber = ONBOARDING_STEPS.indexOf(activeStep) + 1;
  document.getElementById(eyebrowId).textContent = `Крок ${stepNumber} з ${total}`;
  dotIds.forEach((id, idx) => {
    const dot = document.getElementById(id);
    // Beyond the current total (password not known to be needed yet), keep
    // the dot out of the strip entirely rather than showing a 4th segment
    // that will never light up.
    dot.hidden = idx >= total;
    dot.classList.toggle("active", ONBOARDING_STEPS[idx] === activeStep);
  });
}
