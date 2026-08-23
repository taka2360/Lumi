/**
 * The panel windows' connection to Core (ADR-042).
 *
 * Settings, inspector and memory each open their own window and each hold their own
 * connection, as role `panel`. **They do not connect as `stage`**: the server keeps one
 * connection per role, and a second `stage` client would take the character's — which is
 * exactly why these views used to be drawn inside the character window.
 *
 * | method | kind | meaning |
 * |---|---|---|
 * | `panel.settings.state` | notify | The effective settings, and where each value came from |
 * | `panel.inspector.state` | notify | Activity tree and latency breakdown |
 * | `panel.memory.state` | notify | Memory changed underneath this window |
 *
 * **Nothing here decides anything**, the same rule the character window follows: every
 * value shown was broadcast by Core.
 */

import { useEffect } from "react";

import { connectToCore } from "../core/connection";
import {
  METHOD_PANEL_INSPECTOR,
  METHOD_PANEL_MEMORY,
  METHOD_PANEL_SETTINGS,
} from "../core/methods";
import { toInspectorSnapshot, toSettingsSnapshot } from "../core/payloads";
import { clearRequester, setRequester } from "../core/request";
import { useStageStore } from "../core/store";

export function usePanelConnection(): void {
  useEffect(() => {
    const store = useStageStore.getState();

    const connection = connectToCore(
      {
        onConnectedChange: (connected) => store.setConnected(connected),
        notifications: {
          [METHOD_PANEL_SETTINGS]: (payload) => store.setSettings(toSettingsSnapshot(payload)),
          [METHOD_PANEL_INSPECTOR]: (payload) => store.setInspector(toInspectorSnapshot(payload)),
          // **A nudge, not the data.** What the memory window should show depends on the
          // page and filter it is on, which Core has no reason to know.
          [METHOD_PANEL_MEMORY]: () => store.nudgeMemory(),
        },
        // **Core never commands a panel** (ADR-042): with several windows open there is
        // no "the" answer, so there is nothing to register here.
        commands: {},
      },
      "panel",
    );

    setRequester(connection.request, "panel");

    return () => {
      clearRequester();
      connection.close();
    };
  }, []);
}
