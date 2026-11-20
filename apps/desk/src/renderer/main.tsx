import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme } from "./theme";
import "./styles/fonts.css";
import "./styles/app.css";

// Apply tokens before first paint so nothing renders unthemed.
applyTheme(document.documentElement.style);

const root = document.getElementById("root");
if (root === null) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
