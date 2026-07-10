import { nameInitials, escapeHtml } from "./render.js";

// Adapted from DESIGN's member-list.js. DESIGN's mock only ever had a
// free-text `label` per member and fabricated a nickname/initials from it.
// Real members (MemberOut: user_id, name, tag, pending) already carry real
// identity — generateNickname()/getInitials() are gone, nameInitials()
// (render.js, already used by the working app) replaces getInitials(), and
// there is no nickname to show (the real client-add flow takes a Telegram
// identifier, not a display name the way DESIGN's form did — see
// group-detail's #member-input placeholder in index.html).
//
// Mirrors app/db/models.py::CLIENT_TAG — only client rows get a remove
// button; staff members are managed via /tag in the group chat, not from
// here (same rule the pre-redesign mainApp.js enforced).
const CLIENT_TAG = "Клієнт";

export function renderMemberList(container, members) {
  container.innerHTML = "";

  if (members.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state center";
    empty.setAttribute("role", "img");
    empty.setAttribute("aria-label", "Учасників поки немає");
    empty.innerHTML = `<i class="fa-solid fa-user-group" aria-hidden="true"></i>`;
    container.appendChild(empty);
    return;
  }

  members.forEach((member) => {
    const row = document.createElement("div");
    row.className = "member-row";

    const isClient = member.tag === CLIENT_TAG;
    const badgeClass = isClient ? "role-badge-client" : "role-badge-staff";
    // pending — запрошення відправлено (invite_link), але людина ще не
    // приєдналась до чату. Той самий .pending-badge, що й "очікує" на
    // картці групи (group-list.js) для awaiting_response.
    const pendingBadge = member.pending
      ? `<span class="pending-badge">Очікує</span>`
      : "";

    row.innerHTML = `
      <div class="member-info">
        <span class="icon-list-button center" aria-hidden="true">${escapeHtml(nameInitials(member.name))}</span>
        <div class="label-list-button-stack">
          <span class="label-list-button">${escapeHtml(member.name)}</span>
        </div>
      </div>
      <div class="member-actions">
        ${pendingBadge}
        <span class="role-badge ${badgeClass}">${escapeHtml(member.tag)}</span>
      </div>
    `;

    if (isClient) {
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "member-delete center";
      // data-member-id — той самий підхід, що й data-group-id у group-list.js:
      // клік однозначно визначає учасника через його реальний user_id, а не
      // позицію рядка серед дітей контейнера.
      removeBtn.dataset.memberId = member.user_id;
      removeBtn.setAttribute("aria-label", `Видалити ${member.name}`);
      removeBtn.innerHTML = `<i class="fa-solid fa-trash" aria-hidden="true"></i>`;
      row.querySelector(".member-actions").appendChild(removeBtn);
    }

    container.appendChild(row);
  });
}
