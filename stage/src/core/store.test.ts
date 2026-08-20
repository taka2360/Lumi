/**
 * The store itself — **the places where writing one value also changes another.**
 *
 * Reading payloads is `payloads.test.ts`. What is left here is the store's own
 * behaviour, and in Phase 1 that is exactly one rule: **Lumi starting to speak is what
 * clears what the user said.** It comes from a Core event rather than a Stage-side
 * timer, because the Stage decides nothing (docs/architecture/ui.md §2).
 */

import { describe, expect, it } from "vitest";

import { toSpeech } from "./payloads";
import { useStageStore } from "./store";

describe("what the user said", () => {
  it("Lumi starting to speak clears it", () => {
    // **The turn changing hands is a Core event, not a Stage-side timer.**
    const store = useStageStore.getState();
    store.setUserSaid({ text: "おはよう", startedAtMs: 0 });
    store.setSpeech(toSpeech({ text: "おはよう！" }, 1));
    expect(useStageStore.getState().userSaid).toBeNull();
  });

  it("speech ending does not bring it back and does not clear it either", () => {
    const store = useStageStore.getState();
    store.setSpeech(null);
    store.setUserSaid({ text: "きこえてる？", startedAtMs: 0 });
    useStageStore.getState().setSpeech(null);
    expect(useStageStore.getState().userSaid).toEqual({ text: "きこえてる？", startedAtMs: 0 });
  });
});
