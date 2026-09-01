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

import { cachedLocale, setDocumentLocale } from "../i18n";
import { LocaleProvider } from "../i18n/provider";
import { Inspector } from "../inspector/Inspector";
import { Memory } from "../memory/Memory";
import { mountRoot, rootElement } from "../mount";
import { PANEL_KINDS, type PanelKind } from "../platform/PlatformShell";
import { Settings } from "../settings/Settings";
import { PanelShell } from "./PanelShell";
import "../styles/tokens.css";
import "./panel.css";

// **The same starting guess the other windows make.** `LocaleProvider` corrects this as
// soon as Core sends the setting, but until then the document would otherwise claim the
// language its HTML was authored in rather than the one about to be rendered.
setDocumentLocale(cachedLocale());

const container = rootElement();

/** **Fail loudly.** A page that loaded the panel bundle without saying which panel it is
 * has nothing sensible to render, and rendering an empty window would hide the mistake.
 * The list of kinds is `PANEL_KINDS` (`platform/PlatformShell.ts`) — **the same one Shell is held
 * to** — so a fourth panel cannot be added there and silently fail to render here. */
function isPanelKind(value: string | undefined): value is PanelKind {
  return PANEL_KINDS.some((kind) => kind === value);
}

function content(kind: PanelKind) {
  switch (kind) {
    case "settings":
      return <PanelShell titleKey="settings.title">{<Settings />}</PanelShell>;
    case "inspector":
      return <PanelShell titleKey="inspector.title">{<Inspector />}</PanelShell>;
    case "memory":
      return <PanelShell titleKey="memory.title">{<Memory />}</PanelShell>;
  }
}

const kind = container.dataset.panel;
if (!isPanelKind(kind)) {
  throw new Error(`Unknown panel: ${String(kind)}`);
}

mountRoot(<LocaleProvider>{content(kind)}</LocaleProvider>, container);
