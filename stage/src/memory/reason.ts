/**
 * The failure Core gave, as it gave it.
 *
 * **"Could not do that" on its own is indistinguishable from the button not being wired
 * up.** Core sends reason codes rather than sentences (ADR-036), and showing the code
 * beats swallowing it.
 */
export function reason(error: unknown): string {
  return error instanceof Error ? error.message : "failed";
}
