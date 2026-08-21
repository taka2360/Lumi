/**
 * Reading the Inspector snapshot. **docs/architecture/ui.md §5.**
 *
 * The Inspector is the view people reach for *while something is already wrong*, so
 * **it must survive a payload it does not fully understand.** Throwing here would take
 * the tool away exactly when it is needed.
 */

import { describe, expect, it } from "vitest";

import { toInspectorSnapshot } from "../core/payloads";

const ACTIVITY = {
  id: "a1",
  kind: "conversation",
  actor: "user_initiated",
  intent: "reply",
  state: "running",
  priority: 70,
  foreground: true,
  cancellables: [{ label: "TTS playback", contract: "hard", finished: false }],
};

describe("the activity tree", () => {
  it("reads an activity and its children", () => {
    const view = toInspectorSnapshot({ activities: [ACTIVITY], latency: null });
    const activity = view.activities.at(0);
    expect(activity?.kind).toBe("conversation");
    expect(activity?.foreground).toBe(true);
    expect(activity?.cancellables).toEqual([
      { label: "TTS playback", contract: "hard", finished: false },
    ]);
  });

  it("keeps a child whose state disagrees with its parent", () => {
    // ★ **That divergence is the point of the view.** A parent already cancelling while
    // a child has not stopped is what cannot be diagnosed from logs.
    const view = toInspectorSnapshot({
      activities: [
        {
          ...ACTIVITY,
          state: "cancelling",
          cancellables: [{ label: "tool", contract: "non_cancellable", finished: false }],
        },
      ],
    });
    expect(view.activities.at(0)?.state).toBe("cancelling");
    expect(view.activities.at(0)?.cancellables.at(0)?.finished).toBe(false);
  });

  it("an empty tree is not an error", () => {
    expect(toInspectorSnapshot({}).activities).toEqual([]);
    expect(toInspectorSnapshot({ activities: "nope" }).activities).toEqual([]);
  });

  it("a malformed entry never takes the whole view down", () => {
    // **The view has to survive being handed something odd**, since that is precisely
    // when someone is looking at it.
    const view = toInspectorSnapshot({ activities: [ACTIVITY, null, 42, {}] });
    expect(view.activities).toHaveLength(2);
    expect(view.activities.at(1)?.kind).toBe("?");
  });
});

describe("the latency breakdown", () => {
  it("separates the spans from the summary", () => {
    // Core flattens the spans into the payload, so **anything numeric that is not a
    // summary field is a span.**
    const view = toInspectorSnapshot({
      activities: [],
      latency: {
        correlation_id: "c1",
        vad_ms: 401,
        stt_ms: 82,
        measured_sum_ms: 1675,
        total_ms: 1675,
        unaccounted_ms: 0,
        completed: true,
      },
    });
    expect(view.latency?.spans).toEqual({ vad_ms: 401, stt_ms: 82 });
    expect(view.latency?.total_ms).toBe(1675);
    expect(view.latency?.completed).toBe(true);
  });

  it("a span Core added but the Stage does not know is still carried", () => {
    // **Silently dropping it would hide the measurement someone just added.**
    const view = toInspectorSnapshot({ latency: { brand_new_ms: 12 } });
    expect(view.latency?.spans.brand_new_ms).toBe(12);
  });

  it("keeps a negative unaccounted_ms rather than clamping it", () => {
    // **Clamping would hide the bug that produced it** (agent/latency.py).
    expect(toInspectorSnapshot({ latency: { unaccounted_ms: -40 } }).latency?.unaccounted_ms).toBe(
      -40,
    );
  });

  it("an interrupted turn is marked, not discarded", () => {
    // Barge-in is the normal case, and **how far the turn got is the measurement.**
    expect(toInspectorSnapshot({ latency: { total_ms: 300 } }).latency?.completed).toBe(false);
  });

  it("no turn yet is null, not an empty breakdown", () => {
    expect(toInspectorSnapshot({ activities: [] }).latency).toBeNull();
  });
});
