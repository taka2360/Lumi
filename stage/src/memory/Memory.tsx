/**
 * The memory window — **what Lumi remembers, and the user's hand on it** (ui.md §5b).
 *
 * Design → docs/architecture/memory.md §8 / docs/contracts/privacy.md §5
 *
 * > ユーザーが誤った記憶を直せないと、間違いが永久に残る
 *
 * ## What this window decides: nothing
 *
 * Every row was sent by Core, every button is a request Core is free to refuse, and the
 * list is re-read from Core after each change rather than patched here. Editing a row in
 * place would mean the screen and the database can disagree — and the whole reason this
 * window exists is to be able to trust what it says.
 *
 * ## Two things it must never do
 *
 * **Never claim something is deleted before Core says so.** A row that disappears
 * optimistically and comes back on the next search is worse than a slow one.
 *
 * **Never erase without showing what goes first.** The preview comes from Core, lists
 * every category including the empty ones, and the confirmation token it returns is what
 * the erase request has to carry back (`EraseDialog`).
 *
 * ## What is where
 *
 * Reading the list is `useMemorySearch`; one row is `MemoryRow`; the erase dialog and its
 * keyboard behaviour are `EraseDialog` / `useFocusTrap`. **What is left here is the
 * window itself** — the two whole-store actions, and which of the empty states applies.
 */

import { useCallback, useState } from "react";

import {
  METHOD_PANEL_MEMORY_ERASE,
  METHOD_PANEL_MEMORY_ERASE_PREVIEW,
  METHOD_PANEL_MEMORY_EXPORT,
} from "../core/methods";
import { callCore } from "../core/request";
import { useStageStore } from "../core/store";
import { translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { EraseDialog } from "./EraseDialog";
import { MemoryRow } from "./MemoryRow";
import { type EraseTarget, toEraseTargets } from "./payloads";
import { reason } from "./reason";
import { useMemorySearch } from "./useMemorySearch";

export function Memory() {
  const locale = useLocale();
  const connected = useStageStore((state) => state.connected);
  const search = useMemorySearch(connected);

  const [notice, setNotice] = useState<string | null>(null);
  const [erasing, setErasing] = useState<{ targets: EraseTarget[]; confirmation: string } | null>(
    null,
  );

  /**
   * Whether one of the two whole-store actions is still running.
   *
   * **Neither shows anything while it works**, and both walk every memory there is. A
   * second click on [書き出す] writes a second file nobody asked for; a second click on
   * [全部消す] sends a second erase over the first — and being able to start an erase
   * while the export is still reading is worse than either.
   */
  const [busy, setBusy] = useState(false);

  const { setFailure, reload } = search;
  const onChanged = useCallback(() => {
    setNotice(null);
    reload();
  }, [reload]);

  const exportAll = async () => {
    setFailure(null);
    setBusy(true);
    try {
      const answer = await callCore(METHOD_PANEL_MEMORY_EXPORT);
      setNotice(
        translate(locale, "memory.exported", {
          path: String(answer.path ?? ""),
          count: Number(answer.count ?? 0),
        }),
      );
    } catch (error: unknown) {
      setFailure(reason(error));
    } finally {
      setBusy(false);
    }
  };

  const previewErase = async () => {
    setFailure(null);
    try {
      const answer = await callCore(METHOD_PANEL_MEMORY_ERASE_PREVIEW);
      setErasing({
        targets: toEraseTargets(answer),
        confirmation: String(answer.confirmation ?? ""),
      });
    } catch (error: unknown) {
      setFailure(reason(error));
    }
  };

  const eraseEverything = async (confirmation: string) => {
    setBusy(true);
    try {
      await callCore(METHOD_PANEL_MEMORY_ERASE, { confirmation });
      setErasing(null);
      setNotice(translate(locale, "memory.erased"));
      reload();
    } catch (error: unknown) {
      setFailure(reason(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="memory">
      <div className="memory__controls">
        <input
          className="memory__search"
          type="search"
          value={search.query}
          placeholder={translate(locale, "memory.searchPlaceholder")}
          aria-label={translate(locale, "memory.search")}
          onChange={(event) => search.setQuery(event.target.value)}
        />
        <label className="memory__toggle">
          <input
            type="checkbox"
            checked={search.includeHistory}
            onChange={(event) => search.setIncludeHistory(event.target.checked)}
          />
          {translate(locale, "memory.includeHistory")}
        </label>
      </div>

      {search.failure && (
        <p className="panel__status panel__status--bad" role="alert">
          {translate(locale, "memory.failed", { reason: search.failure })}
        </p>
      )}
      {notice && <p className="panel__status">{notice}</p>}

      {search.items.length === 0 ? (
        // **Three different empty states, because they mean three different things.**
        // A window that renders "nothing remembered" while disconnected, or while a
        // conversation is still waiting to be read, invites the conclusion that memory
        // is broken.
        <p className="inspect__empty">
          {!connected
            ? translate(locale, "memory.disconnected")
            : search.query
              ? translate(locale, "memory.noMatch")
              : search.pending > 0
                ? translate(locale, "memory.pending", { count: search.pending })
                : translate(locale, "memory.empty")}
        </p>
      ) : (
        <>
          <p className="memory__count">
            {translate(locale, "memory.count", {
              shown: search.items.length,
              total: search.total,
            })}
          </p>
          <ul className="memory__list">
            {search.items.map((item) => (
              <MemoryRow
                key={item.id}
                item={item}
                onChanged={onChanged}
                onFailed={search.setFailure}
              />
            ))}
          </ul>
          {search.hasMore && (
            <button type="button" onClick={search.showMore}>
              {translate(locale, "memory.more")}
            </button>
          )}
        </>
      )}

      <footer className="memory__footer">
        <button type="button" disabled={busy} onClick={() => void exportAll()}>
          {translate(locale, "memory.export")}
        </button>
        {/* **Said before it happens**, not in the success message. */}
        <span className="memory__warning">{translate(locale, "memory.exportWarning")}</span>
        <button
          type="button"
          className="memory__danger"
          disabled={busy}
          onClick={() => void previewErase()}
        >
          {translate(locale, "memory.eraseAll")}
        </button>
      </footer>

      {erasing && (
        <EraseDialog
          targets={erasing.targets}
          busy={busy}
          onErase={() => void eraseEverything(erasing.confirmation)}
          onCancel={() => setErasing(null)}
        />
      )}
    </div>
  );
}
