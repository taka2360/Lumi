import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { cachedLocale, setDocumentLocale } from "../i18n";
import { Help } from "./Help";
import "./help.css";

setDocumentLocale(cachedLocale());

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

createRoot(container).render(
  <StrictMode>
    <Help />
  </StrictMode>,
);
