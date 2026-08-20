import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { cachedLocale, setDocumentLocale } from "./i18n";
import { LocaleProvider } from "./i18n/provider";
import "./styles.css";

setDocumentLocale(cachedLocale());

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

createRoot(container).render(
  <StrictMode>
    <LocaleProvider>
      <App />
    </LocaleProvider>
  </StrictMode>,
);
