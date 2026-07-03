import { apiFetch, initData } from "./api.js";
import { showStep } from "./navigation.js";
import { CHEVRON_SVG, escapeHtml, roleInitials } from "./render.js";
import { resetConnectStep } from "./connect.js";
import { enterMainApp } from "./mainApp.js";
import { renderOnboardingProgress } from "./progress.js";

// --- Onboarding: everything (registration, role, account connect) happens
// here in the Mini App — there is no chat-based flow to fall back to. ---

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
  // `restart: true` here means "this is a fresh app open, not a step the
  // user just completed" — see routeByProfile for why that distinction
  // matters.
  await routeByProfile(me, { restart: true });
}

export async function routeByProfile(me, options = {}) {
  // Registration (role + connected account) is treated as one atomic flow:
  // if it isn't fully finished, reopening the Mini App always restarts at
  // role selection rather than resuming at whatever step was left off
  // (e.g. jumping straight to phone entry because a role was already
  // picked in an earlier, abandoned session). `options.restart` is only
  // set by `bootstrap()` on a fresh open — calls from *within* the flow
  // itself (`selectRole` advancing to the connect step right after picking
  // a role) omit it, so picking a role doesn't just loop back here.
  if (!me.role || (options.restart && !me.is_connected)) {
    await showRoleStep();
    return;
  }
  if (!me.is_connected) {
    resetConnectStep();
    showStep("connect-step");
    return;
  }
  await enterMainApp(me, options.initialTab);
}

// `editing: true` is used when this screen is opened from the "Змінити"
// button in Profile settings (an already-onboarded user changing their
// role), as opposed to first-time onboarding via `routeByProfile()`. In
// that mode we drop the "Крок 1 з 2" onboarding framing in favour of
// settings-appropriate copy, and reveal a back button so the user can
// bail out without changing anything.
export async function showRoleStep(options = {}) {
  const editing = Boolean(options.editing);
  const res = await apiFetch("/users/roles");
  const roles = await res.json();
  const list = document.getElementById("role-list");
  list.innerHTML = "";

  roles.forEach((role) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.className = "list-row";
    button.innerHTML =
      `<span class="icon-pill">${escapeHtml(roleInitials(role))}</span>` +
      `<span class="row-label">${escapeHtml(role.value)}</span>` +
      `<span class="chevron">${CHEVRON_SVG}</span>`;
    button.addEventListener("click", () => selectRole(role.name, button, { editing }));
    li.appendChild(button);
    list.appendChild(li);
  });

  document.getElementById("role-step-back-btn").hidden = !editing;
  document.getElementById("role-step-eyebrow").hidden = editing;
  // The step counter/progress dots only make sense during first-time
  // registration — "Змінити посаду" from Profile settings is an existing
  // user tweaking a setting, not a step in a multi-step flow.
  document.getElementById("role-step-dots").hidden = editing;
  document.getElementById("role-step-title").textContent = editing ? "Зміна посади" : "Обери посаду";
  document.getElementById("role-step-text").textContent = editing
    ? "Обери нову посаду зі списку нижче."
    : "Це потрібно один раз — щоб бот знав, хто ти в команді, і показував потрібні функції.";

  if (!editing) {
    renderOnboardingProgress("role", "role-step-eyebrow", [
      "role-dot-role",
      "role-dot-phone",
      "role-dot-code",
      "role-dot-password",
    ]);
  }

  showStep("role-step");
}

async function selectRole(roleName, button, options = {}) {
  button.disabled = true;
  const res = await apiFetch("/users/role", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: roleName }),
  });

  if (!res.ok) {
    button.disabled = false;
    return;
  }

  const me = await res.json();
  await routeByProfile(me, { initialTab: options.editing ? "profile" : "groups" });
}
