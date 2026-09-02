/**
 * Sizes, as the setup screens show them. **Pure** — the wording of a number is exactly
 * the sort of thing worth testing without a renderer.
 */

import { type Locale, translate } from "../i18n";

export function formatGigabytes(bytes: number, locale: Locale): string {
  return `${(bytes / 1_000_000_000).toFixed(1)} ${translate(locale, "setup.model.gb")}`;
}

/**
 * A size in whichever unit keeps it readable.
 *
 * The four things Lumi fetches span three orders of magnitude — a 196 MiB embedding model
 * next to a 6.6 GB language model. **"0.2 GB" next to "6.6 GB" hides that difference**,
 * and this list exists so people can see where the total comes from.
 */
export function formatSize(bytes: number, locale: Locale): string {
  return bytes >= 1_000_000_000
    ? formatGigabytes(bytes, locale)
    : `${Math.round(bytes / 1_000_000)} ${translate(locale, "setup.model.mb")}`;
}
