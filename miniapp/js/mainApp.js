import { apiFetch, safeJson } from "./api.js";
import { showStep } from "./navigation.js";
import {
  CHEVRON_SVG,
  GROUP_ICON_SVG,
  TRASH_ICON_SVG,
  escapeHtml,
  nameInitials,
  skeletonListItems,
} from "./render.js";
import { showRoleStep } from "./onboarding.js";
import { tg } from "./telegram.js";

// --- Main app: groups (tab) / profile (tab) / group detail (drill-down) ---

let selectedGroupId = null;

// Mirrors app/db/models.py::CLIENT_TAG — only client rows get a remove
// button; staff members are managed via /tag, not this delete flow.
const CLIENT_TAG = "Клієнт";

// Status texts ("Групу створено.", "Клієнта додано.", error messages, etc.)
// should auto-dismiss so they don't linger on screen forever. Each status
// element gets its own pending-timeout slot here so that setting the text
// again (e.g. "Створюю…" immediately followed by the result) cancels any
// previously scheduled clear instead of stacking timeouts that could wipe
// out a newer message early.
const statusClearTimeouts = new WeakMap();
const STATUS_AUTO_CLEAR_MS = 5000;

function setStatus(el, text, kind) {
  const pending = statusClearTimeouts.get(el);
  if (pending) clearTimeout(pending);

  el.textContent = text;
  el.classList.remove("ok", "error");
  if (kind) el.classList.add(kind);

  if (text) {
    const timeoutId = setTimeout(() => {
      el.textContent = "";
      el.classList.remove("ok", "error");
      statusClearTimeouts.delete(el);
    }, STATUS_AUTO_CLEAR_MS);
    statusClearTimeouts.set(el, timeoutId);
  }
}

export async function enterMainApp(me, initialTab = "groups") {
  document.getElementById("profile-avatar").textContent = nameInitials(me.full_name, me.username);
  document.getElementById("profile-name").textContent = me.full_name || me.username || String(me.id);
  document.getElementById("profile-role").textContent = me.role;
  document.getElementById("profile-role-label").textContent = me.role;
  document.getElementById("connection-status-pill").textContent = me.is_connected ? "Активно" : "Не підключено";

  showStep("main-app");
  switchTab(initialTab);
  await fetchGroups();
}

document.getElementById("change-role-btn").addEventListener("click", () => showRoleStep({ editing: true }));
document.getElementById("role-step-back-btn").addEventListener("click", () => {
  showStep("main-app");
  switchTab("profile");
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(name) {
  document.getElementById("screen-groups").hidden = name !== "groups";
  document.getElementById("screen-profile").hidden = name !== "profile";
  document.getElementById("screen-group-detail").hidden = true;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.getElementById("tabbar").hidden = false;
}

document.getElementById("back-to-groups-btn").addEventListener("click", () => switchTab("groups"));

async function fetchGroups() {
  const list = document.getElementById("groups");
  list.innerHTML = skeletonListItems();
  const res = await apiFetch("/groups");
  if (!res.ok) {
    list.innerHTML = '<li class="list-empty">Не вдалося завантажити групи.</li>';
    return;
  }
  renderGroups(await res.json());
}

function renderGroups(groups) {
  const list = document.getElementById("groups");
  list.innerHTML = "";

  if (groups.length === 0) {
    list.innerHTML = '<li class="list-empty">Поки немає жодної групи. Створи першу вище.</li>';
    return;
  }

  groups.forEach((g) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.className = "list-row";
    button.innerHTML =
      `<span class="icon-pill">${GROUP_ICON_SVG}</span>` +
      `<span class="row-label">${escapeHtml(g.title)}</span>` +
      (g.awaiting_response ? '<span class="badge" title="Очікує відповіді клієнта">очікує</span>' : "") +
      `<span class="chevron">${CHEVRON_SVG}</span>`;
    button.addEventListener("click", () => openGroupDetail(g.id, g.title));
    li.appendChild(button);
    list.appendChild(li);
  });
}

async function openGroupDetail(groupId, title) {
  selectedGroupId = groupId;
  document.getElementById("group-detail-title").textContent = title;
  setStatus(document.getElementById("members-status"), "");
  setStatus(document.getElementById("add-client-status"), "");
  document.getElementById("client-identifier").value = "";
  setStatus(document.getElementById("delete-group-status"), "");

  document.getElementById("screen-groups").hidden = true;
  document.getElementById("screen-profile").hidden = true;
  document.getElementById("screen-group-detail").hidden = false;
  document.getElementById("tabbar").hidden = true;

  await fetchMembers(groupId);
}

async function fetchMembers(groupId) {
  const list = document.getElementById("members");
  list.innerHTML = skeletonListItems();
  const res = await apiFetch(`/members?group_id=${groupId}`);
  if (!res.ok) {
    list.innerHTML = '<li class="list-empty">Не вдалося завантажити учасників.</li>';
    return;
  }
  renderMembers(await res.json());
}

function renderMembers(members) {
  const list = document.getElementById("members");
  list.innerHTML = "";

  if (members.length === 0) {
    list.innerHTML = '<li class="list-empty">Учасників ще немає.</li>';
    return;
  }

  members.forEach((m) => {
    const li = document.createElement("li");
    li.className = "member-row";
    li.innerHTML =
      `<div class="member-avatar">${escapeHtml(nameInitials(m.name))}</div>` +
      `<div style="flex:1"><div class="member-name">${escapeHtml(m.name)}</div></div>` +
      (m.pending ? '<span class="badge" title="Ще не приєднався(лась) до групи">очікує</span>' : "") +
      `<span class="role-badge">${escapeHtml(m.tag)}</span>`;

    // Only clients can be removed from here — staff membership is managed
    // via /register + /tag in the group chat itself, not from this list.
    if (m.tag === CLIENT_TAG) {
      const removeBtn = document.createElement("button");
      removeBtn.className = "member-remove-btn";
      removeBtn.type = "button";
      removeBtn.setAttribute("aria-label", `Видалити клієнта ${m.name}`);
      removeBtn.innerHTML = TRASH_ICON_SVG;
      removeBtn.addEventListener("click", () => confirmRemoveMember(m.user_id, m.name));
      li.appendChild(removeBtn);
    }

    list.appendChild(li);
  });
}

function confirmRemoveMember(userId, name) {
  const confirmMessage = `Видалити клієнта «${name}» з групи?`;
  // Same native-confirm-with-fallback pattern as deleteGroupBtn's handler below.
  if (tg?.showConfirm) {
    tg.showConfirm(confirmMessage, (confirmed) => {
      if (confirmed) removeMember(userId);
    });
  } else if (window.confirm(confirmMessage)) {
    removeMember(userId);
  }
}

async function removeMember(userId) {
  const status = document.getElementById("members-status");
  setStatus(status, "Видаляю…");

  const res = await apiFetch("/members/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_id: selectedGroupId, user_id: userId }),
  });

  const data = await safeJson(res);
  if (res.ok) {
    setStatus(status, "Клієнта видалено.", "ok");
    await fetchMembers(selectedGroupId);
  } else {
    setStatus(status, `Помилка: ${data.detail || "спробуй ще раз."}`, "error");
  }
}

const createGroupBtn = document.getElementById("create-group-btn");
createGroupBtn.addEventListener("click", async () => {
  const titleInput = document.getElementById("group-title");
  const title = titleInput.value.trim();
  const status = document.getElementById("create-status");
  if (!title) {
    setStatus(status, "Вкажи назву групи.", "error");
    return;
  }

  createGroupBtn.disabled = true;
  setStatus(status, "Створюю…");
  const res = await apiFetch("/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

  const data = await safeJson(res);
  createGroupBtn.disabled = false;
  if (res.ok) {
    setStatus(status, "Групу створено.", "ok");
    titleInput.value = "";
    await fetchGroups();
  } else {
    setStatus(status, `Помилка: ${data.detail || "спробуй ще раз."}`, "error");
  }
});

const addClientBtn = document.getElementById("add-client-btn");
addClientBtn.addEventListener("click", async () => {
  const identifierInput = document.getElementById("client-identifier");
  const identifier = identifierInput.value.trim();
  const status = document.getElementById("add-client-status");
  if (!selectedGroupId || !identifier) return;

  addClientBtn.disabled = true;
  setStatus(status, "Додаю…");
  const res = await apiFetch("/members/add-client", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_id: selectedGroupId, identifier }),
  });

  const data = await safeJson(res);
  addClientBtn.disabled = false;
  if (res.ok) {
    setStatus(
      status,
      data.invite_link ? `Перешли клієнту посилання: ${data.invite_link}` : "Клієнта додано.",
      "ok"
    );
    identifierInput.value = "";
    await fetchMembers(selectedGroupId);
  } else {
    setStatus(status, `Помилка: ${data.detail || "спробуй ще раз."}`, "error");
  }
});

const deleteGroupBtn = document.getElementById("delete-group-btn");
deleteGroupBtn.addEventListener("click", () => {
  if (!selectedGroupId) return;

  const confirmMessage = "Видалити цю групу? Цю дію не можна скасувати.";
  // Telegram's own confirm dialog looks native inside the Mini App; fall
  // back to the browser's confirm() for older clients that predate
  // showConfirm (same defensive-guard pattern as telegram.js's bootstrap).
  if (tg?.showConfirm) {
    tg.showConfirm(confirmMessage, (confirmed) => {
      if (confirmed) deleteSelectedGroup();
    });
  } else if (window.confirm(confirmMessage)) {
    deleteSelectedGroup();
  }
});

async function deleteSelectedGroup() {
  const status = document.getElementById("delete-group-status");
  deleteGroupBtn.disabled = true;
  setStatus(status, "Видаляю…");

  const res = await apiFetch(`/groups/${selectedGroupId}`, { method: "DELETE" });
  const data = await safeJson(res);
  deleteGroupBtn.disabled = false;

  if (res.ok) {
    setStatus(status, "Групу видалено.", "ok");
    switchTab("groups");
    await fetchGroups();
  } else {
    setStatus(status, `Помилка: ${data.detail || "спробуй ще раз."}`, "error");
  }
}
