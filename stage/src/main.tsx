import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { browserLocale, setDocumentLocale } from "./i18n";
import "./styles.css";

setDocumentLocale(browserLocale());

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root が見つからない");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
