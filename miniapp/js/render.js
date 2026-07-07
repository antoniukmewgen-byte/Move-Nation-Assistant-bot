// --- small render helpers shared by several screens ---

export const CHEVRON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>';

export const GROUP_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/></svg>';

export const TRASH_ICON_SVG =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
  '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/></svg>';

// Curated 2-3 letter abbreviations for the fixed set of roles in app.db.models.Role,
// keyed by the enum member name (stable) rather than the Ukrainian label (display
// text, could change wording without changing the identifier). Anything not in this
// map (e.g. a role added later without updating this list) falls back to a generic
// derivation so the UI never breaks, just looks slightly less polished.
const ROLE_INITIALS = {
  KERIVNYK: "КР",
  MANAGER: "МН",
  DIAGNOST: "ДГ",
  TEAMLEAD: "ТЛ",
  SEO: "SEO",
  SALES: "ВП",
  SALES_HEAD: "КВ",
};

export function roleInitials(role) {
  if (ROLE_INITIALS[role.name]) return ROLE_INITIALS[role.name];
  const words = role.value.trim().split(/\s+/);
  return words.length > 1 ? (words[0][0] + words[1][0]).toUpperCase() : role.value.slice(0, 2).toUpperCase();
}

export function nameInitials(fullName, username) {
  const source = (fullName || username || "").trim();
  if (!source) return "?";
  const words = source.split(/\s+/);
  return words.length > 1 ? (words[0][0] + words[1][0]).toUpperCase() : source.slice(0, 2).toUpperCase();
}

export function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}
