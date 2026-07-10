import { roleInitials } from "./render.js";

// Adapted from DESIGN's role-list.js. DESIGN's mock had a static ROLES
// array of {code, label}; the real backend returns RoleOut objects
// ({name, value}) from GET /users/roles — `name` is the enum key used for
// the POST /users/role body, `value` is the Ukrainian display label.
// roleInitials() (render.js, already used by the working app) derives the
// same kind of short icon abbreviation DESIGN's `code` was.
export function renderRoleList(container, roles) {
  container.innerHTML = "";

  roles.forEach((role) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-button";
    button.setAttribute("role", "radio");
    button.setAttribute("aria-checked", "false");
    // data-role-name — той самий підхід, що й data-role-code у DESIGN, лише
    // значення тепер role.name (enum-ключ), бо саме його чекає POST /users/role.
    button.dataset.roleName = role.name;
    button.innerHTML = `
      <div class="button-container">
        <span class="icon-list-button center" aria-hidden="true">${roleInitials(role)}</span>
        <span class="label-list-button">${role.value}</span>
      </div>
      <i class="fa-solid fa-chevron-right" aria-hidden="true"></i>
    `;
    container.appendChild(button);
  });
}
