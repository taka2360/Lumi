/**
 * The live connection's `request`, for UI that asks Core to change something (ADR-028).
 *
 * ## Why this is module state and not context
 *
 * A button deep in the tree needs it, and threading a callback through every component to
 * reach one button is worse than this. **`null` while disconnected**, and callers have to
 * handle that rather than pretending the change went through.
 *
 * ## Why the role is kept alongside it
 *
 * The same control appears in two windows: settings can be changed from the character
 * window's own settings snapshot and from the settings window. **The method name differs
 * by namespace** (`stage.settings.update` / `panel.settings.update`), because the
 * namespace is what tells Core which window asked, and a role may only send its own
 * (ADR-042). Keeping the role here means the caller asks for "update settings" rather
 * than knowing which window it happens to be running in.
 */

import type { CoreConnection } from "./connection";
import { METHOD_PANEL_SETTINGS_UPDATE, METHOD_SETTINGS_UPDATE } from "./methods";
import type { CoreRole } from "./protocol";

interface Requester {
  request: CoreConnection["request"];
  role: CoreRole;
}

let active: Requester | null = null;

export function setRequester(request: CoreConnection["request"], role: CoreRole): void {
  active = { request, role };
}

export function clearRequester(): void {
  active = null;
}

/** Asks Core to do something. **Rejects when nothing is connected** (never a silent no-op). */
export async function callCore(
  method: string,
  payload: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  if (!active) {
    throw new Error("not_connected");
  }
  return active.request(method, payload);
}

/** Asks Core to change settings. **Rejects if Core refused** (never silently no-ops). */
export async function updateSettings(changes: Record<string, string>): Promise<void> {
  if (!active) {
    throw new Error("not_connected");
  }
  const method = active.role === "panel" ? METHOD_PANEL_SETTINGS_UPDATE : METHOD_SETTINGS_UPDATE;
  await active.request(method, { changes });
}
