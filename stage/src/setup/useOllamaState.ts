/**
 * What Ollama is doing, and the polling that notices when that changes.
 *
 * **Lumi never fetches Ollama** (ADR-023), so the only thing it can do while Ollama is
 * missing or stopped is watch for it. That makes this the one screen with a timer, and
 * the reason it was tangled through the middle of the panel component.
 *
 * The four states are distinguished because the sentence differs for each: not installed,
 * installed but not running, starting up, and running while the model is being checked.
 * **Collapsing them would tell someone to install what they already have.**
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { SetupSnapshot } from "../core/store";
import { recheckOllama } from "../core/useCoreConnection";
import { useOpenOllamaSite } from "../platform/useStageShell";

/** Short enough to notice an installation finishing, slow enough to stay negligible. */
export const OLLAMA_RECHECK_INTERVAL_MS = 1_000;

export interface OllamaState {
  /** Nothing to wait for on Lumi's side — the user has to act. */
  waiting: boolean;
  /** Running; Lumi is looking for the model. */
  checking: boolean;
  /** Coming up on its own. */
  starting: boolean;
  /** Installed, but the process is not running. **Telling this user to install is wrong.** */
  installedButStopped: boolean;
  /** The last attempt to open the download page or re-check failed. */
  actionFailed: boolean;
  openSite: () => void;
}

export function useOllamaState(setup: SetupSnapshot, promptOpen: boolean): OllamaState {
  const [actionFailed, setActionFailed] = useState(false);
  const checkActive = useRef(false);
  const openSite = useOpenOllamaSite(() => setActionFailed(true));

  const starting =
    setup.llm.state === "detected" &&
    setup.llm.runtime === "starting" &&
    setup.llm.reason === "ollama_starting";
  const waiting =
    setup.llm.state === "not_configured" ||
    (setup.llm.state === "detected" && setup.llm.runtime === "stopped") ||
    starting;
  const checking = setup.llm.state === "detected" && setup.llm.runtime === "starting" && !starting;
  const installedButStopped = setup.llm.state === "detected" && setup.llm.runtime === "stopped";

  const check = useCallback(async () => {
    // **One check at a time.** A slow answer must not queue another behind it.
    if (checkActive.current) return;
    checkActive.current = true;
    setActionFailed(false);
    try {
      await recheckOllama();
    } catch {
      setActionFailed(true);
    } finally {
      checkActive.current = false;
    }
  }, []);

  useEffect(() => {
    // **Only while this screen is the one on display.** A timer running behind a question,
    // or once startup has moved on, is work nobody is waiting for.
    if (promptOpen || setup.boot !== "blocked" || !waiting) return;
    const timer = window.setInterval(() => void check(), OLLAMA_RECHECK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [check, waiting, promptOpen, setup.boot]);

  return { waiting, checking, starting, installedButStopped, actionFailed, openSite };
}
