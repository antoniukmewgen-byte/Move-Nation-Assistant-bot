// Top-level screen switching, shared by onboarding and the main app.

const TOP_STEPS = ["loading", "role-step", "connect-step", "main-app"];

export function showStep(id) {
  TOP_STEPS.forEach((sectionId) => {
    document.getElementById(sectionId).hidden = sectionId !== id;
  });
}
