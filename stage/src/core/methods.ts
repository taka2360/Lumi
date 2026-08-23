/**
 * The `stage.*` names that go on the wire. **`docs/contracts/wire.json` is
 * authoritative** (→ ADR-022); this file is the Stage-side mirror of it, and the
 * Core-side mirror is `core/lumi/transport/methods.py`.
 *
 * ## Why these are constants and not string literals
 *
 * Handler maps are keyed by these names, and **a typo is silently dropped as an
 * unknown method** (`unhandled_method`) rather than raising. Nothing breaks loudly;
 * the setup screen just never updates. Naming them once, in a file `wire.test.ts`
 * cross-checks against the contract, is what turns that into a test failure.
 */

/** Setup state for all three components, plus the derived boot phase. */
export const METHOD_SETUP_STATE = "stage.setup.state";
/** Asks whether to fetch a component. **A command** — Core waits for the answer. */
export const METHOD_SETUP_PROMPT = "stage.setup.prompt";
export const METHOD_SPEECH_STARTED = "stage.speech.started";
export const METHOD_SPEECH_ENDED = "stage.speech.ended";
/** What Core heard the user say. */
export const METHOD_USER_SAID = "stage.user.said";
/** Which model to draw, decided by Core from the Content Pack (ADR-029). */
export const METHOD_MODEL = "stage.character.model";
export const METHOD_EXPRESSION = "stage.character.expression";
/**
 * The effective settings and where each value came from.
 *
 * **Still sent to the character window** even though settings live in their own window
 * now (ADR-042): `locale` decides the language of everything drawn here.
 */
export const METHOD_SETTINGS = "stage.settings.state";

/** Whether the microphone is open, and whether the user muted it (ui.md §5b). */
export const METHOD_MIC = "stage.audio.mic";

/** Core → panel windows (ADR-042). */
export const METHOD_PANEL_SETTINGS = "panel.settings.state";
export const METHOD_PANEL_INSPECTOR = "panel.inspector.state";
/** Memory changed under an open memory window. **A nudge; the window asks for the data.** */
export const METHOD_PANEL_MEMORY = "panel.memory.state";

/** Stage → Core (ADR-028). */
export const METHOD_SETTINGS_UPDATE = "stage.settings.update";

/** Mute or unmute the microphone, from beside the indicator that says it is open. */
export const METHOD_MIC_MUTE = "stage.audio.mute";

/** Panel → Core (ADR-042). **A different namespace, because a different window asks.** */
export const METHOD_PANEL_SETTINGS_UPDATE = "panel.settings.update";
export const METHOD_PANEL_MEMORY_SEARCH = "panel.memory.search";
export const METHOD_PANEL_MEMORY_EDIT = "panel.memory.edit";
export const METHOD_PANEL_MEMORY_FORGET = "panel.memory.forget";
export const METHOD_PANEL_MEMORY_CONFIRM = "panel.memory.confirm";
export const METHOD_PANEL_MEMORY_EXPORT = "panel.memory.export";
export const METHOD_PANEL_MEMORY_ERASE_PREVIEW = "panel.memory.erase_preview";
export const METHOD_PANEL_MEMORY_ERASE = "panel.memory.erase";

/**
 * The windows `openPanel` can open. **Shell holds the same three**, and an unknown value
 * opens nothing (docs/interfaces/shell.md).
 */
export const PANEL_KINDS = ["settings", "inspector", "memory"] as const;
export type PanelKind = (typeof PANEL_KINDS)[number];
/** Re-runs the fixed local Ollama detection; no URL or host is supplied by Stage. */
export const METHOD_SETUP_RECHECK_OLLAMA = "stage.setup.recheck_ollama";

/**
 * The answer to whether to fetch. `select` is used for a model already present locally;
 * any other non-install answer means "not now".
 *
 * The panel labels `CHOICE_INSTALL` "retry" after a failure — **same choice**, and the
 * retry count is deliberately unbounded since the press always comes from the user (ADR-034).
 */
export const CHOICE_INSTALL = "install";
export const CHOICE_SKIP = "skip";
export const CHOICE_SELECT = "select";

/**
 * Why Core says there is no model to draw (ADR-036). **Core sends a code; the wording
 * is ours**, so a language change reaches this line like it reaches every other.
 *
 * Listed for the contract test. **Nothing branches on the list** — `modelReasonText`
 * renders an unrecognised code verbatim rather than checking membership first.
 */
export const CHARACTER_MODEL_REASONS = ["model_not_in_pack", "pack_unreadable"] as const;
