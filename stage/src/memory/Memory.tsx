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
 * the erase request has to carry back.
 */

import { useCallback, useEffect, useState } from "react";

import {
  METHOD_PANEL_MEMORY_CONFIRM,
  METHOD_PANEL_MEMORY_EDIT,
  METHOD_PANEL_MEMORY_ERASE,
  METHOD_PANEL_MEMORY_ERASE_PREVIEW,
  METHOD_PANEL_MEMORY_EXPORT,
  METHOD_PANEL_MEMORY_FORGET,
  METHOD_PANEL_MEMORY_SEARCH,
} from "../core/methods";
import { callCore } from "../core/request";
import { useStageStore } from "../core/store";
import { hasMessage, translate } from "../i18n";
import { useLocale } from "../i18n/provider";
import { type EraseTarget, type MemoryItem, toEraseTargets, toMemoryPage } from "./payloads";

/** How many rows one request asks for. The window grows a page at a time. */
const PAGE = 50;

function reason(error: unknown): string {
  return error instanceof Error ? error.message : "failed";
}

function Row({
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

export function Memory() {
  const locale = useLocale();
  const connected = useStageStore((state) => state.connected);
  // **Core says memory changed; this window decides what that means for its own view.**
  const revision = useStageStore((state) => state.memoryRevision);

  const [query, setQuery] = useState("");
  const [includeHistory, setIncludeHistory] = useState(false);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [erasing, setErasing] = useState<{ targets: EraseTarget[]; confirmation: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    if (!connected) {
      return;
    }
    try {
      const answer = await callCore(METHOD_PANEL_MEMORY_SEARCH, {
        query,
        include_history: includeHistory,
        limit,
      });
      const page = toMemoryPage(answer);
      setItems(page.items);
      setTotal(page.total);
      setFailure(null);
    } catch (error: unknown) {
      setFailure(reason(error));
    }
  }, [connected, includeHistory, limit, query]);

  // **Re-read rather than patched.** After an edit, a delete, or a reflection pass, what
  // the list should show is Core's answer, not this component's guess at it.
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (revision > 0) {
      void load();
    }
  }, [revision, load]);

  const onChanged = useCallback(() => {
    setNotice(null);
    void load();
  }, [load]);

  const exportAll = async () => {
    setFailure(null);
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
    try {
      await callCore(METHOD_PANEL_MEMORY_ERASE, { confirmation });
      setErasing(null);
      setNotice(translate(locale, "memory.erased"));
      void load();
    } catch (error: unknown) {
      setFailure(reason(error));
    }
  };

  return (
    <div className="memory">
      <div className="memory__controls">
        <input
          className="memory__search"
          type="search"
          value={query}
          placeholder={translate(locale, "memory.searchPlaceholder")}
          aria-label={translate(locale, "memory.search")}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label className="memory__toggle">
          <input
            type="checkbox"
            checked={includeHistory}
            onChange={(event) => setIncludeHistory(event.target.checked)}
          />
          {translate(locale, "memory.includeHistory")}
        </label>
      </div>

      {failure && (
        <p className="panel__status panel__status--bad" role="alert">
          {translate(locale, "memory.failed", { reason: failure })}
        </p>
      )}
      {notice && <p className="panel__status">{notice}</p>}

      {items.length === 0 ? (
        <p className="inspect__empty">
          {translate(locale, query ? "memory.noMatch" : "memory.empty")}
        </p>
      ) : (
        <>
          <p className="memory__count">
            {translate(locale, "memory.count", { shown: items.length, total })}
          </p>
          <ul className="memory__list">
            {items.map((item) => (
              <Row key={item.id} item={item} onChanged={onChanged} onFailed={setFailure} />
            ))}
          </ul>
          {items.length < total && (
            <button type="button" onClick={() => setLimit(limit + PAGE)}>
              {translate(locale, "memory.more")}
            </button>
          )}
        </>
      )}

      <footer className="memory__footer">
        <button type="button" onClick={() => void exportAll()}>
          {translate(locale, "memory.export")}
        </button>
        {/* **Said before it happens**, not in the success message. */}
        <span className="memory__warning">{translate(locale, "memory.exportWarning")}</span>
        <button type="button" className="memory__danger" onClick={() => void previewErase()}>
          {translate(locale, "memory.eraseAll")}
        </button>
      </footer>

      {erasing && (
        <div className="memory__erase" role="dialog" aria-modal="true">
          <h2>{translate(locale, "memory.erasePreview")}</h2>
          <ul>
            {erasing.targets.map((target) => {
              const key = `memory.target.${target.target}`;
              return (
                <li key={target.target}>
                  {translate(locale, "memory.eraseRow", {
                    target: hasMessage(key) ? translate(locale, key) : target.target,
                    count: target.count,
                  })}
                </li>
              );
            })}
          </ul>
          <p>{translate(locale, "memory.eraseConfirm")}</p>
          <div className="memory__actions">
            <button
              type="button"
              className="memory__danger"
              onClick={() => void eraseEverything(erasing.confirmation)}
            >
              {translate(locale, "memory.eraseAll")}
            </button>
            <button type="button" onClick={() => setErasing(null)}>
              {translate(locale, "memory.cancel")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
