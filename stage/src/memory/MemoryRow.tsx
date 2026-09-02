/**
 * One remembered thing, and the user's hand on it.
 *
 * **Every button is a request Core is free to refuse**, and nothing is patched in place:
 * the parent re-reads the list once Core says the change happened. A row that changed on
 * screen before the database agreed would undo the reason this window exists.
 */

import { useCallback, useState } from "react";

import {
  METHOD_PANEL_MEMORY_CONFIRM,
  METHOD_PANEL_MEMORY_EDIT,
  METHOD_PANEL_MEMORY_FORGET,
} from "../core/methods";
import { callCore } from "../core/request";
import { hasMessage, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import type { MemoryItem } from "./payloads";
import { reason } from "./reason";

export function MemoryRow({
  item,
  onChanged,
  onFailed,
}: {
  item: MemoryItem;
  onChanged: () => void;
  onFailed: (message: string) => void;
}) {
  const locale = useLocale();
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async (work: Promise<unknown>) => {
      setBusy(true);
      try {
        await work;
        setDraft(null);
        onChanged();
      } catch (error: unknown) {
        // **The reason Core gave, shown as it was given.** "Could not do that" on its own
        // is indistinguishable from the button not being wired up.
        onFailed(reason(error));
      } finally {
        setBusy(false);
      }
    },
    [onChanged, onFailed],
  );

  const modeKey = `memory.mode.${item.assertion_mode}`;
  const editing = draft !== null;

  return (
    <li className={item.superseded_by ? "memory__row memory__row--old" : "memory__row"}>
      <div className="memory__head">
        <span className="memory__subject">{item.subject}</span>
        <span className="memory__mode">
          {hasMessage(modeKey) ? translate(locale, modeKey) : item.assertion_mode}
        </span>
        {item.trust_level !== "trusted" && (
          <span className="memory__taint">{translate(locale, "memory.trust.tainted")}</span>
        )}
        {item.superseded_by && (
          <span className="memory__flag">{translate(locale, "memory.superseded")}</span>
        )}
        {item.archived && (
          <span className="memory__flag">{translate(locale, "memory.archived")}</span>
        )}
      </div>

      {editing ? (
        <textarea
          className="memory__editor"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label={translate(locale, "memory.edit")}
        />
      ) : (
        <p className="memory__content">{item.content}</p>
      )}

      <div className="memory__meta">
        <span title={translate(locale, "memory.salience")}>
          {translate(locale, "memory.salience")} {item.effective_salience.toFixed(2)}
        </span>
        <span>{new Date(item.valid_from).toLocaleString(locale)}</span>
      </div>

      <div className="memory__actions">
        {editing ? (
          <>
            <button
              type="button"
              disabled={busy || draft.trim() === ""}
              onClick={() =>
                void run(callCore(METHOD_PANEL_MEMORY_EDIT, { id: item.id, content: draft }))
              }
            >
              {translate(locale, "memory.save")}
            </button>
            <button type="button" disabled={busy} onClick={() => setDraft(null)}>
              {translate(locale, "memory.cancel")}
            </button>
          </>
        ) : (
          <>
            <button type="button" disabled={busy} onClick={() => setDraft(item.content)}>
              {translate(locale, "memory.edit")}
            </button>
            {/* **The escalation to `user_confirmed`** (Invariant 7). Already-confirmed
                memories do not offer it again: there is nothing stronger to become. */}
            {item.assertion_mode !== "user_confirmed" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(callCore(METHOD_PANEL_MEMORY_CONFIRM, { id: item.id }))}
              >
                {translate(locale, "memory.confirm")}
              </button>
            )}
            <button
              type="button"
              className="memory__danger"
              disabled={busy}
              onClick={() => {
                // **Asked before, not undone after.** The deletion is physical.
                if (window.confirm(translate(locale, "memory.forgetConfirm"))) {
                  void run(callCore(METHOD_PANEL_MEMORY_FORGET, { id: item.id }));
                }
              }}
            >
              {translate(locale, "memory.forget")}
            </button>
          </>
        )}
      </div>
    </li>
  );
}
