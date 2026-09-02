/**
 * Reading the memory list from Core.
 *
 * **The list is re-read, never patched.** After an edit, a delete, or a reflection pass,
 * what the window should show is Core's answer rather than this component's guess at it —
 * and the whole reason the window exists is to be able to trust what it says.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { METHOD_PANEL_MEMORY_SEARCH } from "../core/methods";
import { callCore } from "../core/request";
import { useStageStore } from "../core/store";
import { type MemoryItem, toMemoryPage } from "./payloads";
import { reason } from "./reason";

/** How many rows one request asks for. The window grows a page at a time. */
const PAGE = 50;

/**
 * How long to wait before searching for what is being typed.
 *
 * **Short enough not to feel like lag, long enough that a word is one search.** Every
 * keystroke otherwise asks Core to scan the memory table.
 */
export const SEARCH_DEBOUNCE_MS = 200;

export interface MemorySearch {
  query: string;
  setQuery(value: string): void;
  includeHistory: boolean;
  setIncludeHistory(value: boolean): void;
  items: MemoryItem[];
  total: number;
  /** Conversations Core has not turned into memories yet. **Not the same as "empty".** */
  pending: number;
  /** Whether another page exists. */
  hasMore: boolean;
  showMore(): void;
  failure: string | null;
  setFailure(value: string | null): void;
  /** Re-reads the current page. */
  reload(): void;
}

export function useMemorySearch(connected: boolean): MemorySearch {
  // **Core says memory changed; this window decides what that means for its own view.**
  const revision = useStageStore((state) => state.memoryRevision);

  const [query, setQuery] = useState("");
  const [includeHistory, setIncludeHistory] = useState(false);
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [pending, setPending] = useState(0);
  const [limit, setLimit] = useState(PAGE);
  const [failure, setFailure] = useState<string | null>(null);

  /**
   * Which search is the current one.
   *
   * **Answers arrive in whatever order Core finishes them.** Typing "fac" fires three
   * searches, and without this the list ends up showing whichever came back last rather
   * than the one matching what is in the box.
   */
  const latest = useRef(0);

  const load = useCallback(async () => {
    if (!connected) {
      return;
    }
    const request = ++latest.current;
    try {
      const answer = await callCore(METHOD_PANEL_MEMORY_SEARCH, {
        query,
        include_history: includeHistory,
        limit,
      });
      if (request !== latest.current) {
        return;
      }
      const page = toMemoryPage(answer);
      setItems(page.items);
      setTotal(page.total);
      setPending(page.pendingTurns);
      setFailure(null);
    } catch (error: unknown) {
      if (request !== latest.current) {
        return;
      }
      setFailure(reason(error));
    }
  }, [connected, includeHistory, limit, query]);

  // **One effect, and `revision` is one of its inputs.** Two effects both depending on
  // `load` fired twice for every keystroke, because a changed `load` re-ran both.
  //
  // The delay debounces typing. It applies to every trigger rather than only to the
  // query, which costs a reflection nudge a fraction of a second and keeps this to one
  // rule instead of two.
  useEffect(() => {
    // **`revision` is read here on purpose.** It is a signal rather than an input: a new
    // value means Core said memory changed, and re-running the search is the whole
    // response. Reading it is also what makes it an honest dependency instead of one the
    // linter has to be told about.
    void revision;
    const timer = window.setTimeout(() => void load(), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [load, revision]);

  return {
    query,
    setQuery,
    includeHistory,
    setIncludeHistory,
    items,
    total,
    pending,
    hasMore: items.length < total,
    showMore: () => setLimit(limit + PAGE),
    failure,
    setFailure,
    reload: () => void load(),
  };
}
