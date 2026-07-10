import { apiFetch, safeJson } from "./api.js";
import { showStep } from "./navigation.js";
import { switchTab } from "./tabs.js";
import { fadeOut } from "./transitions.js";
import { setupStepForm } from "./step-form.js";
import { renderGroupList } from "./group-list.js";
import { renderMemberList } from "./member-list.js";
import { nameInitials } from "./render.js";
import { connectRealtime } from "./realtime.js";
import { tg } from "./telegram.js";
import { showToast } from "./toast.js";

// --- Main app: groups (tab) / profile (tab) / group detail (own top-level
// section, DESIGN's #group-detail) — DESIGN markup + real API wiring. ---

let selectedGroupId = null;

// enterMainApp() below runs again every time the user returns here (e.g.
// right after finishing registration) — the realtime socket must only ever
// be opened once per Mini App session, not re-opened on every such return.
let realtimeStarted = false;

// Mirrors app/db/models.py::CLIENT_TAG — only client rows get a remove
// button; staff members are managed via /tag in the group chat, not here.
const CLIENT_TAG = "Клієнт";

const groupListEl = document.getElementById("group-list");
const groupsCountEl = document.getElementById("groups-count");
const awaitingCountEl = document.getElementById("awaiting-count");
const groupInputEl = document.getElementById("group-input");
const groupSubmitEl = document.getElementById("group-submit");
const groupErrorEl = document.getElementById("group-error");

const profileAvatarEl = document.getElementById("profile-avatar");
const profileNameEl = document.getElementById("profile-name");
const profileRoleEl = document.getElementById("profile-role");
const settingsRoleValueEl = document.getElementById("settings-role-value");
const connectionStatusBadgeEl = document.getElementById("connection-status-badge");
const profilePhoneValueEl = document.getElementById("profile-phone-value");

const groupDetailTitleEl = document.getElementById("group-detail-title");
const groupDetailBackEl = document.getElementById("group-detail-back");
const groupDetailMenuEl = document.getElementById("group-detail-menu");
const groupDetailDropdownEl = document.getElementById("group-detail-dropdown");
const groupDetailDeleteEl = document.getElementById("group-detail-delete");
const memberInputEl = document.getElementById("member-input");
const memberSubmitEl = document.getElementById("member-submit");
const memberErrorEl = document.getElementById("member-error");
const memberListEl = document.getElementById("member-list");

// --- shared empty/loading/error states for the two lists (groups, members) ---

function renderListLoading(container) {
  container.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "empty-state center";
  loading.setAttribute("role", "status");
  loading.setAttribute("aria-label", "Завантаження");
  loading.innerHTML = `<div class="spinner"></div>`;
  container.appendChild(loading);
}

function renderListError(container, message) {
  container.innerHTML = "";
  const errorEl = document.createElement("div");
  errorEl.className = "empty-state center";
  errorEl.setAttribute("role", "alert");
  errorEl.setAttribute("aria-label", message);
  errorEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i>`;
  container.appendChild(errorEl);
}

// A fetch that resolves quickly (the common case) should show nothing in
// between. But a *first* load with no visible result until the response
// arrives is bad if the network is slow: this arms a small spinner after a
// short delay. Skipped entirely when the list already has real rows — a
// realtime-triggered background refresh must never be interrupted by a
// spinner replacing already-visible content.
const SLOW_FETCH_SPINNER_DELAY_MS = 400;

function showSpinnerIfSlow(container) {
  if (container.querySelector(".list-button, .member-row")) return () => {};
  const timer = window.setTimeout(() => renderListLoading(container), SLOW_FETCH_SPINNER_DELAY_MS);
  return () => window.clearTimeout(timer);
}

// --- profile ---

function applyProfile(me) {
  if (profileAvatarEl) profileAvatarEl.textContent = nameInitials(me.full_name, me.username);
  if (profileNameEl) profileNameEl.textContent = me.full_name || me.username || String(me.id);
  if (profileRoleEl) profileRoleEl.textContent = me.role;
  if (settingsRoleValueEl) settingsRoleValueEl.textContent = me.role;
  if (connectionStatusBadgeEl) {
    connectionStatusBadgeEl.textContent = me.is_connected ? "Активно" : "Не підключено";
    connectionStatusBadgeEl.classList.toggle("role-badge-active", me.is_connected);
    connectionStatusBadgeEl.classList.toggle("role-badge-inactive", !me.is_connected);
  }
  // /users/me не повертає телефон (UserMeOut без поля phone) — з'явиться
  // останнім кроком цього переносу дизайну, разом з бекенд-полем.
  if (profilePhoneValueEl) profilePhoneValueEl.textContent = me.phone || "—";
}

export async function refreshProfile() {
  const res = await apiFetch("/users/me");
  if (!res.ok) return;
  applyProfile(await res.json());
}

export async function enterMainApp(me, initialTab = "groups") {
  applyProfile(me);
  showStep("main");
  switchTab(initialTab);
  await fetchGroups();

  if (!realtimeStarted) {
    realtimeStarted = true;
    connectRealtime({
      groups_changed: fetchGroups,
      members_changed: (event) => {
        // Only the group detail screen the user currently has open cares
        // about a members_changed push for some other group_id.
        if (event.group_id === selectedGroupId) fetchMembers(selectedGroupId);
      },
      profile_changed: refreshProfile,
    });
  }
}

// --- groups tab ---

function updateGroupsStats(groups) {
  if (groupsCountEl) groupsCountEl.textContent = groups.length;
  if (awaitingCountEl) awaitingCountEl.textContent = groups.filter((g) => g.awaiting_response).length;
}

async function fetchGroups() {
  if (!groupListEl) return;
  const cancelSpinner = showSpinnerIfSlow(groupListEl);
  const res = await apiFetch("/groups");
  cancelSpinner();
  if (!res.ok) {
    renderListError(groupListEl, "Не вдалося завантажити групи");
    return;
  }
  const groups = await res.json();
  renderGroupList(groupListEl, groups);
  updateGroupsStats(groups);
}

if (groupListEl) {
  groupListEl.addEventListener("click", (event) => {
    const button = event.target.closest(".list-button");
    if (!button) return;

    const groupId = Number(button.dataset.groupId);
    const title = button.querySelector(".label-list-button")?.textContent ?? "";
    openGroupDetail(groupId, title);
  });
}

function isNonEmptyGroupTitle(value) {
  return value.trim().length > 0;
}

// Уточнення "2 - лишаємо унікальність ід" з реальним бекендом: назва групи
// не перевіряється на дублікат (лише непорожність) — унікальність тримається
// на реальному id групи, а не на назві, як було в DESIGN-мокапі.
setupStepForm({
  inputEl: groupInputEl,
  submitEl: groupSubmitEl,
  errorEl: groupErrorEl,
  isValid: isNonEmptyGroupTitle,
  errorMessage: "Введи назву групи",
  busyText: "Створюю...",
  submitAction: async (rawTitle) => {
    const title = rawTitle.trim();
    const res = await apiFetch("/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const data = await safeJson(res);
    if (!res.ok) throw new Error(data.detail || "Не вдалося створити групу.");
    return title;
  },
  onComplete: async (title) => {
    groupInputEl.value = "";
    showToast(`Групу «${title}» створено`);
    await fetchGroups();
  },
});

// --- group detail (drill-down) ---

async function openGroupDetail(groupId, title) {
  selectedGroupId = groupId;
  if (groupDetailTitleEl) groupDetailTitleEl.textContent = title;
  if (memberInputEl) memberInputEl.value = "";
  if (memberErrorEl && !memberErrorEl.hidden) fadeOut(memberErrorEl);
  if (memberListEl) memberListEl.innerHTML = "";

  showStep("group-detail");
  await fetchMembers(groupId);
}

if (groupDetailBackEl) {
  groupDetailBackEl.addEventListener("click", () => {
    closeGroupMenu();
    showStep("main");
  });
}

// "•••" меню — просте показати/сховати; закриваємо по кліку будь-де поза
// меню чи кнопкою (делегування на document).
function closeGroupMenu() {
  if (!groupDetailDropdownEl || groupDetailDropdownEl.hidden) return;
  groupDetailDropdownEl.hidden = true;
  groupDetailMenuEl?.setAttribute("aria-expanded", "false");
}

if (groupDetailMenuEl && groupDetailDropdownEl) {
  groupDetailMenuEl.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = !groupDetailDropdownEl.hidden;
    groupDetailDropdownEl.hidden = isOpen;
    groupDetailMenuEl.setAttribute("aria-expanded", String(!isOpen));
  });

  document.addEventListener("click", (event) => {
    if (groupDetailDropdownEl.hidden) return;
    if (event.target.closest("#group-detail-dropdown, #group-detail-menu")) return;
    closeGroupMenu();
  });
}

if (groupDetailDeleteEl) {
  groupDetailDeleteEl.addEventListener("click", () => {
    if (selectedGroupId === null) return;

    const confirmMessage = "Видалити цю групу? Цю дію не можна скасувати.";
    // Telegram's own confirm dialog looks native inside the Mini App; fall
    // back to the browser's confirm() for older clients that predate showConfirm.
    if (tg?.showConfirm) {
      tg.showConfirm(confirmMessage, (confirmed) => {
        if (confirmed) deleteSelectedGroup();
      });
    } else if (window.confirm(confirmMessage)) {
      deleteSelectedGroup();
    }
  });
}

async function deleteSelectedGroup() {
  const groupId = selectedGroupId;
  const title = groupDetailTitleEl?.textContent ?? "";
  closeGroupMenu();

  const res = await apiFetch(`/groups/${groupId}`, { method: "DELETE" });
  if (!res.ok) {
    const data = await safeJson(res);
    showToast(`Помилка: ${data.detail || "спробуй ще раз."}`, "error");
    return;
  }

  selectedGroupId = null;
  showStep("main");
  switchTab("groups");
  await fetchGroups();
  showToast(`Групу «${title}» видалено`);
}

// --- members ---

async function fetchMembers(groupId) {
  if (!memberListEl) return;
  const cancelSpinner = showSpinnerIfSlow(memberListEl);
  const res = await apiFetch(`/members?group_id=${groupId}`);
  cancelSpinner();
  if (!res.ok) {
    renderListError(memberListEl, "Не вдалося завантажити учасників");
    return;
  }
  renderMemberList(memberListEl, await res.json());
}

if (memberListEl) {
  memberListEl.addEventListener("click", (event) => {
    const deleteBtn = event.target.closest(".member-delete");
    if (!deleteBtn) return;

    const userId = Number(deleteBtn.dataset.memberId);
    const name = deleteBtn.closest(".member-row")?.querySelector(".label-list-button")?.textContent ?? "";
    confirmRemoveMember(userId, name);
  });
}

function confirmRemoveMember(userId, name) {
  const confirmMessage = `Видалити клієнта «${name}» з групи?`;
  if (tg?.showConfirm) {
    tg.showConfirm(confirmMessage, (confirmed) => {
      if (confirmed) removeMember(userId, name);
    });
  } else if (window.confirm(confirmMessage)) {
    removeMember(userId, name);
  }
}

async function removeMember(userId, name) {
  const res = await apiFetch("/members/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_id: selectedGroupId, user_id: userId }),
  });

  const data = await safeJson(res);
  if (res.ok) {
    showToast(`Клієнта «${name}» видалено`);
    await fetchMembers(selectedGroupId);
  } else {
    showToast(`Помилка: ${data.detail || "спробуй ще раз."}`, "error");
  }
}

// identifier потребує реального Telegram-акаунта (username/номер, який
// Telethon може розв'язати), а не довільного імені — див. плейсхолдер
// #member-input в index.html і AddClientRequest на бекенді. Того самого
// підходу з "3 - теж по ід лишаємо": без перевірки на дублікат тут, лише
// непорожність — унікальність визначається реальним user_id.
function isNonEmptyIdentifier(value) {
  return value.trim().length > 0;
}

// Показує посилання-запрошення нативним popup Telegram (лежить поверх Mini
// App, довше й зручніше читати/копіювати, ніж 2.2-секундний toast) — це
// критична інформація, яку треба переслати клієнту, а не просто фідбек про дію.
function announceInviteLink(link) {
  if (tg?.showPopup) {
    tg.showPopup({
      title: "Посилання для клієнта",
      message: `Клієнта додано. Перешли йому це посилання, щоб він приєднався:\n${link}`,
      buttons: [{ type: "close" }],
    });
  } else {
    showToast("Клієнта додано.");
    window.alert(`Перешли клієнту посилання для приєднання:\n${link}`);
  }
}

setupStepForm({
  inputEl: memberInputEl,
  submitEl: memberSubmitEl,
  errorEl: memberErrorEl,
  isValid: isNonEmptyIdentifier,
  errorMessage: "Введи юзернейм клієнта",
  busyText: "Додаю...",
  submitAction: async (rawIdentifier) => {
    const identifier = rawIdentifier.trim();
    const res = await apiFetch("/members/add-client", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: selectedGroupId, identifier }),
    });
    const data = await safeJson(res);
    if (!res.ok) throw new Error(data.detail || "Не вдалося додати клієнта.");
    return data;
  },
  onComplete: async (data) => {
    memberInputEl.value = "";
    if (data?.invite_link) {
      announceInviteLink(data.invite_link);
    } else {
      showToast("Клієнта додано.");
    }
    await fetchMembers(selectedGroupId);
  },
});
