import { apiFetch, safeJson } from "./api.js";
import { showStep } from "./navigation.js";
import { CHEVRON_SVG, GROUP_ICON_SVG, escapeHtml, nameInitials } from "./render.js";
import { showRoleStep } from "./onboarding.js";

// --- Main app: groups (tab) / profile (tab) / group detail (drill-down) ---

let selectedGroupId = null;

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
  document.getElementById("add-client-status").textContent = "";
  document.getElementById("client-identifier").value = "";

  document.getElementById("screen-groups").hidden = true;
  document.getElementById("screen-profile").hidden = true;
  document.getElementById("screen-group-detail").hidden = false;
  document.getElementById("tabbar").hidden = true;

  await fetchMembers(groupId);
}

async function fetchMembers(groupId) {
  const list = document.getElementById("members");
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
      `<span class="role-badge">${escapeHtml(m.tag)}</span>`;
    list.appendChild(li);
  });
}

const createGroupBtn = document.getElementById("create-group-btn");
createGroupBtn.addEventListener("click", async () => {
  const titleInput = document.getElementById("group-title");
  const title = titleInput.value.trim();
  const status = document.getElementById("create-status");
  status.classList.remove("ok", "error");
  if (!title) {
    status.textContent = "Вкажи назву групи.";
    status.classList.add("error");
    return;
  }

  createGroupBtn.disabled = true;
  status.textContent = "Створюю…";
  const res = await apiFetch("/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

  const data = await safeJson(res);
  createGroupBtn.disabled = false;
  if (res.ok) {
    status.textContent = "Групу створено.";
    status.classList.add("ok");
    titleInput.value = "";
    await fetchGroups();
  } else {
    status.textContent = `Помилка: ${data.detail || "спробуй ще раз."}`;
    status.classList.add("error");
  }
});

const addClientBtn = document.getElementById("add-client-btn");
addClientBtn.addEventListener("click", async () => {
  const identifierInput = document.getElementById("client-identifier");
  const identifier = identifierInput.value.trim();
  const status = document.getElementById("add-client-status");
  status.classList.remove("ok", "error");
  if (!selectedGroupId || !identifier) return;

  addClientBtn.disabled = true;
  status.textContent = "Додаю…";
  const res = await apiFetch("/members/add-client", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_id: selectedGroupId, identifier }),
  });

  const data = await safeJson(res);
  addClientBtn.disabled = false;
  if (res.ok) {
    status.textContent = data.invite_link
      ? `Перешли клієнту посилання: ${data.invite_link}`
      : "Клієнта додано.";
    status.classList.add("ok");
    identifierInput.value = "";
    await fetchMembers(selectedGroupId);
  } else {
    status.textContent = `Помилка: ${data.detail || "спробуй ще раз."}`;
    status.classList.add("error");
  }
});
