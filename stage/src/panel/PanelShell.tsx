/**
 * The frame every panel window shares (ADR-042).
 *
 * A heading, a connection light, and the content. **The light is not decoration**: these
 * windows outlive Core restarts, and a settings table that looks the same whether or not
 * anything is listening is a table whose edits silently go nowhere.
 */

import type { ReactNode } from "react";

import { useStageStore } from "../core/store";
import { type MessageKey, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { usePanelConnection } from "./usePanelConnection";

export function PanelShell({ titleKey, children }: { titleKey: MessageKey; children: ReactNode }) {
  usePanelConnection();
  const locale = useLocale();
  const connected = useStageStore((state) => state.connected);

  return (
    <div className="panel-window">
      <header className="panel-window__bar">
        <h1 className="panel-window__title">{translate(locale, titleKey)}</h1>
        <span
          className={
            connected
              ? "panel-window__link panel-window__link--up"
              : "panel-window__link panel-window__link--down"
          }
        >
          {translate(locale, connected ? "panel.connected" : "panel.disconnected")}
        </span>
      </header>
      <main className="panel-window__body">{children}</main>
    </div>
  );
}
