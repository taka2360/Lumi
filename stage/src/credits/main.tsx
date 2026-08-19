import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { browserLocale, setDocumentLocale } from "../i18n";
import { Credits } from "./Credits";
import "./credits.css";

setDocumentLocale(browserLocale());

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root が見つからない");
}

createRoot(container).render(
  <StrictMode>
    <Credits />
  </StrictMode>,
);
