// Whether the password (2FA) step actually exists is only known once the
// QR code has been scanned — Telegram doesn't expose "does this account
// have 2FA enabled?" ahead of that (see connect.js's "password_required"
// handling). Until then the stepper assumes the common case (no 2FA -> 2
// total steps) instead of always counting a step most accounts never see.
let passwordStepNeeded = false;

export function markPasswordStepNeeded() {
  passwordStepNeeded = true;
}

export function getTotalSteps() {
  return passwordStepNeeded ? 3 : 2;
}

// Reset when a fresh registration starts (bootstrap on a brand new open) —
// otherwise a stale flag from a previous, abandoned attempt in the same tab
// session could keep the 4th step around for an account that doesn't need it.
export function resetPasswordStepNeeded() {
  passwordStepNeeded = false;
}
