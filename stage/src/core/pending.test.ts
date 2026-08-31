/**
 * The request bookkeeping — **the part of the connection with the most ways to go wrong
 * and, until now, no test at all** (reaching it meant standing up a WebSocket first).
 */

import { describe, expect, it, vi } from "vitest";

import { PendingRequests, type Timers } from "./pending";
import type { CoreResult } from "./protocol";

/** Time the test drives by hand, so a 10-second timeout costs nothing to check. */
function fakeTimers() {
  const scheduled = new Map<number, () => void>();
  let next = 1;
  const timers: Timers = {
    setTimeout(handler) {
      const handle = next++;
      scheduled.set(handle, handler);
      return handle;
    },
    clearTimeout(handle) {
      scheduled.delete(handle);
    },
  };
  return {
    timers,
    pendingTimers: () => scheduled.size,
    fireAll() {
      for (const handler of [...scheduled.values()]) {
        handler();
      }
      scheduled.clear();
    },
  };
}

const answer = (
  corrId: string,
  ok: boolean,
  payload: Record<string, unknown> = {},
  error: string | null = null,
): CoreResult => ({ kind: "result", corrId, ok, payload, error });

describe("correlation", () => {
  it("gives every request its own id", () => {
    const pending = new PendingRequests();
    const first = pending.open();
    const second = pending.open();

    expect(first.id).not.toBe(second.id);
    expect(pending.size).toBe(2);
  });

  it("settles only the request the answer names", async () => {
    const pending = new PendingRequests();
    const first = pending.open();
    const second = pending.open();

    expect(pending.settle(answer(second.id, true, { value: 2 }))).toBe(true);

    await expect(second.answered).resolves.toEqual({ value: 2 });
    expect(pending.size).toBe(1);
    // The other one is untouched and still waiting.
    void first.answered.catch(() => {});
  });

  it("drops an answer nobody is waiting for", () => {
    const pending = new PendingRequests();
    // **Normal after a reconnect**, so this reports rather than throws.
    expect(pending.settle(answer("s99", true))).toBe(false);
  });

  it("drops a second answer to the same request", async () => {
    const pending = new PendingRequests();
    const request = pending.open();

    expect(pending.settle(answer(request.id, true, { value: 1 }))).toBe(true);
    expect(pending.settle(answer(request.id, false, {}, "late"))).toBe(false);

    // The first answer stands; the late one cannot turn it into a failure.
    await expect(request.answered).resolves.toEqual({ value: 1 });
  });
});

describe("a refusal rejects", () => {
  it("rejects with the reason Core gave", async () => {
    const pending = new PendingRequests();
    const request = pending.open();

    pending.settle(answer(request.id, false, {}, "unreadable_settings_file"));

    // **Never resolves on `ok: false`** — a caller that forgot to check would otherwise
    // report success for a change Core declined to make.
    await expect(request.answered).rejects.toThrow("unreadable_settings_file");
  });

  it("rejects with a stand-in when no reason came", async () => {
    const pending = new PendingRequests();
    const request = pending.open();

    pending.settle(answer(request.id, false));

    await expect(request.answered).rejects.toThrow("refused");
  });
});

describe("nothing waits forever", () => {
  it("rejects once the timeout passes", async () => {
    const clock = fakeTimers();
    const pending = new PendingRequests(clock.timers);
    const request = pending.open();

    clock.fireAll();

    await expect(request.answered).rejects.toThrow("timeout");
    expect(pending.size).toBe(0);
  });

  it("stops the timer once an answer arrives", () => {
    const clock = fakeTimers();
    const pending = new PendingRequests(clock.timers);
    const request = pending.open();

    pending.settle(answer(request.id, true));

    // A timer left running would fire against an id nobody holds any more.
    expect(clock.pendingTimers()).toBe(0);
    void request.answered.catch(() => {});
  });

  it("ignores an answer that arrives after the timeout", async () => {
    const clock = fakeTimers();
    const pending = new PendingRequests(clock.timers);
    const request = pending.open();

    clock.fireAll();
    await expect(request.answered).rejects.toThrow("timeout");

    expect(pending.settle(answer(request.id, true))).toBe(false);
  });
});

describe("a dropped connection", () => {
  it("fails everything in flight", async () => {
    const pending = new PendingRequests();
    const first = pending.open();
    const second = pending.open();

    pending.abandon("disconnected");

    // **A promise nobody will settle is a spinner that never stops.**
    await expect(first.answered).rejects.toThrow("disconnected");
    await expect(second.answered).rejects.toThrow("disconnected");
    expect(pending.size).toBe(0);
  });

  it("leaves no timer behind", () => {
    const clock = fakeTimers();
    const pending = new PendingRequests(clock.timers);
    const request = pending.open();

    pending.abandon("closed");

    expect(clock.pendingTimers()).toBe(0);
    void request.answered.catch(() => {});
  });

  it("fails a single request whose frame never went out", async () => {
    const pending = new PendingRequests();
    const request = pending.open();

    pending.fail(request.id, "send_failed");

    await expect(request.answered).rejects.toThrow("send_failed");
    expect(pending.size).toBe(0);
  });
});

describe("the timeout is the documented one", () => {
  it("waits ten seconds", () => {
    const setTimeout = vi.fn().mockReturnValue(1);
    const pending = new PendingRequests({ setTimeout, clearTimeout: vi.fn() });
    const request = pending.open();

    expect(setTimeout).toHaveBeenCalledWith(expect.any(Function), 10_000);
    void request.answered.catch(() => {});
  });
});
