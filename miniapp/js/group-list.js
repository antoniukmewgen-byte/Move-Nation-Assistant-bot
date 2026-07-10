import { escapeHtml } from "./render.js";

// Adapted from DESIGN's group-list.js. DESIGN's mock GROUPS had a
// `members: []` array, and the sublabel showed a live "X клієнтів" count.
// The real GroupOut (id, title, status, awaiting_response) has no member
// count — computing one would mean an extra /members request per group on
// every groups-tab load. Instead the sublabel surfaces the one per-group
// signal the backend already gives us for free: whether the group is
// waiting on a reply from the client (awaiting_response) — arguably more
// useful than a raw headcount anyway.
const GROUP_ICONS = ["fa-gavel", "fa-file-contract", "fa-scale-balanced", "fa-stamp", "fa-file-signature", "fa-landmark"];
const GROUP_COLORS = ["#8f7bff", "#33e6c9", "#ffb454", "#ff6bcb", "#5ac8fa", "#c792ea"];

export function renderGroupList(container, groups) {
  container.innerHTML = "";

  if (groups.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state center";
    empty.setAttribute("role", "img");
    empty.setAttribute("aria-label", "Активних груп поки немає");
    empty.innerHTML = `<i class="fa-solid fa-folder-open" aria-hidden="true"></i>`;
    container.appendChild(empty);
    return;
  }

  groups.forEach((group, index) => {
    const icon = GROUP_ICONS[index % GROUP_ICONS.length];
    const color = GROUP_COLORS[index % GROUP_COLORS.length];

    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-button";
    // data-group-id — реальний id групи (не позиція в списку), бо саме він
    // потрібен для /members?group_id= і /groups/{id} при відкритті картки.
    button.dataset.groupId = group.id;
    const statusText = group.awaiting_response ? "Очікує відповіді клієнта" : "Активна";
    button.innerHTML = `
      <div class="button-container">
        <span class="icon-list-button center" aria-hidden="true" style="background: ${color}29; border-color: ${color}4D; color: ${color};">
          <i class="fa-solid ${icon}"></i>
        </span>
        <div class="label-list-button-stack">
          <span class="label-list-button">${escapeHtml(group.title)}</span>
          <span class="sublabel-list-button">${statusText}</span>
        </div>
      </div>
      <i class="fa-solid fa-chevron-right" aria-hidden="true"></i>
    `;
    container.appendChild(button);
  });
}
