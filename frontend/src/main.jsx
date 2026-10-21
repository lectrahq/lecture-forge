import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { applyTheme, loadTheme } from "./theme.js";

// Fonts are installed as packages and bundled with the site, not fetched from a
// font service. The site's Content-Security-Policy allows fonts only from itself,
// so a linked web font would simply be blocked - and Windows often has no decent
// Malayalam font, which matters when Malayalam transcripts are the whole point.
import "@fontsource-variable/inter";
import "@fontsource/noto-sans-malayalam/400.css";
import "@fontsource/noto-sans-malayalam/600.css";

import "./App.css";

// Apply the saved theme before the first render, so the page doesn't flash light.
applyTheme(loadTheme());

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
