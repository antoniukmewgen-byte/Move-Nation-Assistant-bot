import { fadeIn, fadeOut } from "./transitions.js";

// Ported from DESIGN's tabs.js as an ES module — switches the #main
// [data-panel] tabs (Групи / Профіль) with the same fadeOut -> fadeIn
// pattern used for the onboarding step cards, instead of the old app's
// instant hidden-toggle.

const tabbarEl = document.getElementById("tabbar");
const tabButtons = tabbarEl ? Array.from(tabbarEl.querySelectorAll(".tab-btn")) : [];
const tabPanels = Array.from(document.querySelectorAll("#main [data-panel]"));

export function switchTab(tabName) {
  const targetPanel = tabPanels.find((panel) => panel.dataset.panel === tabName);
  const currentPanel = tabPanels.find((panel) => !panel.hidden);
  if (!targetPanel) return;

  tabButtons.forEach((button) => {
    const isActive = button.dataset.tab === tabName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  });

  if (targetPanel === currentPanel) return;

  if (currentPanel) {
    fadeOut(currentPanel, () => fadeIn(targetPanel));
  } else {
    fadeIn(targetPanel);
  }
}

if (tabbarEl) {
  // Делегування на весь таббар, а не обробник на кожній кнопці — узгоджено
  // з тим, як ripple.js уже слухає кліки на document.
  tabbarEl.addEventListener("click", (event) => {
    const button = event.target.closest(".tab-btn");
    if (!button) return;
    switchTab(button.dataset.tab);
  });
}
