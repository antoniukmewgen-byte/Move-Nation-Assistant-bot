import { fadeIn, fadeOut } from "./transitions.js";
import { renderRoleList } from "./role-list.js";
import { fetchRoles, submitRole } from "./onboarding.js";
import { showToast } from "./toast.js";
import { refreshProfile } from "./mainApp.js";

// Adapted from DESIGN's profile.js. DESIGN kept `currentRole` as local mock
// state and just relabeled two spans on click; the real version instead
// POSTs /users/role (same submitRole() helper role-step's step 1 uses, see
// onboarding.js) and, once the server confirms, re-fetches /users/me
// (mainApp.js::refreshProfile) so the profile card, settings row and any
// other open view all agree with the backend, not with an object this
// module invented locally.

const roleEditBtnEl = document.getElementById("role-edit-btn");
const roleModalOverlayEl = document.getElementById("role-modal-overlay");
const roleModalCloseEl = document.getElementById("role-modal-close");
const roleModalListEl = document.getElementById("role-modal-list");

async function openRoleModal() {
  if (!roleModalOverlayEl || !roleModalListEl) return;

  const roles = await fetchRoles();
  renderRoleList(roleModalListEl, roles);

  // Позначаємо поточну посаду вибраною — беремо її з підпису у "Налаштування",
  // а не з окремого локального стану (як currentRole у DESIGN), бо саме цей
  // рядок mainApp.js::enterMainApp/refreshProfile тримає в синхроні з /users/me.
  const currentRoleLabel = document.getElementById("settings-role-value")?.textContent;
  roleModalListEl.querySelectorAll(".list-button").forEach((button) => {
    const label = button.querySelector(".label-list-button")?.textContent;
    button.setAttribute("aria-checked", String(label === currentRoleLabel));
  });

  fadeIn(roleModalOverlayEl);
}

function closeRoleModal() {
  fadeOut(roleModalOverlayEl);
}

if (roleEditBtnEl) {
  roleEditBtnEl.addEventListener("click", openRoleModal);
}

if (roleModalCloseEl) {
  roleModalCloseEl.addEventListener("click", closeRoleModal);
}

// Клік по затемненому фону закриває модалку — перевіряємо, що клікнули саме
// по оверлею, а не по картці всередині нього.
if (roleModalOverlayEl) {
  roleModalOverlayEl.addEventListener("click", (event) => {
    if (event.target === roleModalOverlayEl) closeRoleModal();
  });
}

let roleChangeBusy = false;

if (roleModalListEl) {
  roleModalListEl.addEventListener("click", async (event) => {
    const button = event.target.closest(".list-button");
    if (!button || roleChangeBusy) return;

    roleChangeBusy = true;
    const buttons = roleModalListEl.querySelectorAll(".list-button");
    buttons.forEach((btn) => (btn.disabled = true));

    try {
      await submitRole(button.dataset.roleName);
      await refreshProfile();
      closeRoleModal();
      showToast(`Посаду змінено на «${button.querySelector(".label-list-button").textContent}»`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      roleChangeBusy = false;
      buttons.forEach((btn) => (btn.disabled = false));
    }
  });
}
