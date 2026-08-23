/**
 * The panel windows' entry point (ADR-042).
 *
 * One module for all three, chosen by the `data-panel` attribute the page carries. They
 * differ only in what they render, and three near-identical bootstrap files would be
 * three places to forget the same thing.
 *
 * **`LocaleProvider` is used here, not `useStandaloneLocale`.** Unlike credits, these
 * windows do connect to Core, so the locale is the one Core broadcast rather than a
 * cached guess — a language change in the settings window reaches the memory window
 * without either of them reloading.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { LocaleProvider } from "../i18n/provider";
import { Inspector } from "../inspector/Inspector";
import { Memory } from "../memory/Memory";
import { Settings } from "../settings/Settings";
import { PanelShell } from "./PanelShell";
import "../styles.css";
import "./panel.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

const kind = container.dataset.panel;

/** **Fail loudly.** A page that loaded the panel bundle without saying which panel it is
 * has nothing sensible to render, and rendering an empty window would hide the mistake. */
function content() {
  switch (kind) {
    case "settings":
      return <PanelShell titleKey="settings.title">{<Settings />}</PanelShell>;
    case "inspector":
      return <PanelShell titleKey="inspector.title">{<Inspector />}</PanelShell>;
    case "memory":
      return <PanelShell titleKey="memory.title">{<Memory />}</PanelShell>;
    default:
      throw new Error(`Unknown panel: ${String(kind)}`);
  }
}

createRoot(container).render(
  <StrictMode>
    <LocaleProvider>{content()}</LocaleProvider>
  </StrictMode>,
);
