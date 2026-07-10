import { fadeIn, fadeOut } from "./transitions.js";

// Top-level screen switching, shared by onboarding and the main app. All
// four sections are independent top-level elements in index.html (loading,
// role-step, main, group-detail), so a single generic fadeOut(current) ->
// fadeIn(target) works for every transition between them — this replaces
// the old instant hidden-toggle version with DESIGN's animated pattern
// (same fadeOut/fadeIn calls DESIGN's registration.js/group-detail.js used
// directly, just centralized here since more than two sections need it now).
const TOP_STEPS = ["loading", "role-step", "main", "group-detail"];

export function showStep(id) {
  const target = document.getElementById(id);
  if (!target) return;

  const current = TOP_STEPS.map((sectionId) => document.getElementById(sectionId)).find(
    (el) => el && !el.hidden && el !== target
  );

  if (current) {
    fadeOut(current, () => fadeIn(target));
  } else {
    fadeIn(target);
  }
}
