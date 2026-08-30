import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { cachedLocale, setDocumentLocale, translate } from "../i18n";
import { Help } from "./Help";
import "./help.css";

const locale = cachedLocale();
setDocumentLocale(locale);
document.title = `Lumi — ${translate(locale, "help.title")}`;

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

createRoot(container).render(
  <StrictMode>
    <Help />
  </StrictMode>,
);
