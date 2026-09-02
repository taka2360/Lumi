/**
 * "Erase everything", and what goes with it.
 *
 * **Never erase without showing what goes first** (docs/contracts/privacy.md §5). The
 * preview comes from Core, lists every category including the empty ones, and the
 * confirmation token it returns is what the erase request has to carry back — so this
 * dialog cannot be answered on the strength of a click alone.
 *
 * **Focus starts on Cancel.** This is the one screen in Lumi where the default action is
 * irreversible.
 */

import { useRef } from "react";

import { hasMessage, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import type { EraseTarget } from "./payloads";
import { useFocusTrap } from "./useFocusTrap";

export function EraseDialog({
  targets,
  busy,
  onErase,
  onCancel,
}: {
  targets: EraseTarget[];
  busy: boolean;
  onErase: () => void;
  onCancel: () => void;
}) {
  const locale = useLocale();
  const dialog = useRef<HTMLDivElement | null>(null);
  const cancel = useRef<HTMLButtonElement | null>(null);
  useFocusTrap(true, dialog, cancel, onCancel);

  return (
    <div className="memory__erase" role="dialog" aria-modal="true" ref={dialog}>
      <h2>{translate(locale, "memory.erasePreview")}</h2>
      <ul>
        {targets.map((target) => {
          const key = `memory.target.${target.target}`;
          return (
            <li key={target.target}>
              {translate(locale, "memory.eraseRow", {
                // **An unknown category is shown as itself**, never dropped: a row missing
                // from this list is a row nobody was warned about.
                target: hasMessage(key) ? translate(locale, key) : target.target,
                count: target.count,
              })}
            </li>
          );
        })}
      </ul>
      <p>{translate(locale, "memory.eraseConfirm")}</p>
      <div className="memory__actions">
        <button type="button" className="memory__danger" disabled={busy} onClick={onErase}>
          {translate(locale, "memory.eraseAll")}
        </button>
        <button type="button" ref={cancel} onClick={onCancel}>
          {translate(locale, "memory.cancel")}
        </button>
      </div>
    </div>
  );
}
