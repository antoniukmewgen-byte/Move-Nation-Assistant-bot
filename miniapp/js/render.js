// --- small render helpers shared by several screens ---
// Note: DESIGN's icon SVG constants (chevron/group/trash) were dropped here —
// the ported markup uses FontAwesome <i> icons everywhere instead, so those
// exports would just be dead code.

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
