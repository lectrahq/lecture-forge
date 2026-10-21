// Light / dark theme. "system" follows the operating system's setting.
// The choice is remembered in this browser (localStorage) - nothing is sent to the server.

export const THEMES = ["system", "light", "dark"];
const KEY = "theme";

export function loadTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    return THEMES.includes(saved) ? saved : "system";
  } catch {
    return "system"; // storage blocked (e.g. private mode)
  }
}

export function saveTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // not saved - the theme still applies until the page is reloaded
  }
}

// The CSS (App.css) switches colours based on the data-theme attribute on <html>.
export function applyTheme(theme) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}
