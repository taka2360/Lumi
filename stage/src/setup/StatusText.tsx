/**
 * One status line, with its command or download page underneath when there is one.
 *
 * Shared by every setup screen: **the same state should read the same way** wherever it
 * appears, whether beside an Ollama prompt or in the compact strip.
 */

import type { StatusLine } from "./status";

export function StatusText({ line }: { line: StatusLine }) {
  return (
    <p className={line.tone === "bad" ? "panel__status panel__status--bad" : "panel__status"}>
      {line.text}
      {line.hint && (
        <>
          <br />
          {/* Monospace so it can be typed accurately, and excluded from the window
              gestures so selecting it does not drag Lumi around (ADR-047). */}
          <code className="panel__hint" data-window-gesture="exclude">
            {line.hint}
          </code>
        </>
      )}
    </p>
  );
}
