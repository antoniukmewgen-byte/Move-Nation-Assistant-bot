// Whether the password (2FA) step actually exists is only known once the
// user has submitted their SMS code — Telegram doesn't expose "does this
// account have 2FA enabled?" ahead of that exchange (see connect.js's
// "password_required" handling). Until then the stepper assumes the common
// case (no 2FA -> 3 total steps) instead of always counting a step most
// accounts will never see.
let passwordStepNeeded = false;

export function markPasswordStepNeeded() {
  passwordStepNeeded = true;
}

export function getTotalSteps() {
  return passwordStepNeeded ? 4 : 3;
}

// Reset when a fresh registration starts (bootstrap on a brand new open) —
// otherwise a stale flag from a previous, abandoned attempt in the same tab
// session could keep the 4th step around for an account that doesn't need it.
export function resetPasswordStepNeeded() {
  passwordStepNeeded = false;
}
