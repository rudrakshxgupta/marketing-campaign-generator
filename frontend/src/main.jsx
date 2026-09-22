import React from "react";
import { createRoot } from "react-dom/client";

// Bundled rather than linked from a CDN: this runs on a laptop that may be
// offline, and a missing webfont does not fail loudly -- it silently falls
// back and every size in the design is then wrong by a few percent.
import "@fontsource-variable/inter";

// The same Noto faces the overlay renderer composites with, so the language
// cards preview the actual output typography rather than whatever the OS
// happens to substitute. Nirmala UI sets Devanagari noticeably differently.
import "@fontsource/noto-sans-devanagari";
import "@fontsource/noto-sans-bengali";
import "@fontsource/noto-sans-tamil";
import "@fontsource/noto-sans-telugu";

import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
