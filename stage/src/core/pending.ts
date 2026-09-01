/**
 * Matching Core's answers to the questions the Stage asked (ADR-028).
 *
 * **Separated from the socket because none of it is about the socket.** Allocating a
 * correlation id, holding the promise until the matching `result` arrives, giving up
 * after a timeout, and failing everything in flight when the connection drops are all
 * bookkeeping — and they were the most intricate part of `connection.ts` with no test
 * of their own, because reaching them meant standing up a WebSocket first.
 *
 * The rules, all of which exist because of a way this can go wrong:
 *
 * - **A refusal rejects.** Resolving on `ok: false` would let a caller that forgets to
 *   check report success for a change Core declined to make.
 * - **Nothing waits forever.** The UI has to be able to move on.
 * - **A dropped connection fails everything in flight.** A promise nobody will ever
 *   settle is a spinner that never stops.
 * - **A late or unknown answer is dropped, not thrown.** Arriving after a timeout or a
 *   reconnect is normal, and by then the caller has already been told.
 */

import type { CoreResult } from "./protocol";

/** How long to wait for Core's answer. **Never waits forever** — the UI has to move on. */
export const REQUEST_TIMEOUT_MS = 10_000;

/** The timer functions, so tests can drive time instead of waiting for it. */
export interface Timers {
  setTimeout(handler: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

const browserTimers: Timers = {
  setTimeout: (handler, ms) => window.setTimeout(handler, ms),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

/**
 * The in-flight requests on one connection.
 *
 * Owns the correlation ids as well as the promises: the id is only meaningful as the key
 * that brings the answer back, so nothing outside needs to know how one is made.
 */
export class PendingRequests {
  private nextId = 0;
  private readonly waiting = new Map<string, (result: CoreResult) => void>();

  constructor(
    private readonly timers: Timers = browserTimers,
    private readonly timeoutMs: number = REQUEST_TIMEOUT_MS,
  ) {}

  /** How many answers are still outstanding. */
  get size(): number {
    return this.waiting.size;
  }

  /**
   * Registers a request and hands back the id to put on the wire, plus the promise the
   * caller waits on. **The caller sends the frame**; this only remembers that it did.
   */
  open(): { id: string; answered: Promise<Record<string, unknown>> } {
    const id = `s${this.nextId++}`;
    const answered = new Promise<Record<string, unknown>>((resolve, reject) => {
      const timer = this.timers.setTimeout(() => {
        this.waiting.delete(id);
        reject(new Error("timeout"));
      }, this.timeoutMs);

      this.waiting.set(id, (result) => {
        this.timers.clearTimeout(timer);
        if (result.ok) {
          resolve(result.payload);
        } else {
          reject(new Error(result.error ?? "refused"));
        }
      });
    });
    return { id, answered };
  }

  /**
   * Settles the request this result answers.
   *
   * Returns whether anyone was waiting. **A `false` is not an error**: it is what a
   * result that arrived after its timeout, or after a reconnect, looks like.
   */
  settle(result: CoreResult): boolean {
    const pending = this.waiting.get(result.corrId);
    if (!pending) {
      return false;
    }
    this.waiting.delete(result.corrId);
    pending(result);
    return true;
  }

  /**
   * Fails one request by id. For when the frame could not be sent at all — there is
   * nothing on the wire to answer it, so waiting out the timeout would only delay a
   * failure already known.
   */
  fail(id: string, reason: string): void {
    this.settle({ kind: "result", corrId: id, ok: false, payload: {}, error: reason });
  }

  /** Fails everything in flight. **A dropped connection must not leave a promise hanging.** */
  abandon(reason: string): void {
    for (const [id, resolve] of this.waiting) {
      resolve({ kind: "result", corrId: id, ok: false, payload: {}, error: reason });
    }
    this.waiting.clear();
  }
}
