/**
 * Wording and locale for the Stage. **The entry point** — the catalogs live in
 * `messages.ja.ts` / `messages.en.ts` and locale resolution in `locale.ts`.
 *
 * They were one 590-line file, of which 517 lines were the two catalogs. Splitting them
 * off leaves the part that has behaviour separate from the part that is data, without
 * moving the module anything imports (ADR-036 names this path).
 *
 * **Core sends reason codes, never display strings** (ADR-036). A code the Stage has not
 * learned yet is shown as itself rather than swallowed — see `hasMessage`.
 */

import type { Locale } from "./locale";
import { en } from "./messages.en";
import { ja, type MessageKey } from "./messages.ja";

export {
  browserLocale,
  cachedLocale,
  cacheLocale,
  LOCALE_CACHE_KEY,
  type Locale,
  type LocaleSetting,
  resolveConfiguredLocale,
  resolveLocale,
  SUPPORTED_LOCALES,
} from "./locale";
export type { MessageKey } from "./messages.ja";

/**
 * Whether a message exists for `key`. **For keys built from a value Core sent.**
 *
 * Core's reason codes (ADR-036) are assembled into keys at runtime, so the type system
 * cannot vouch for them: a code the Stage has not learned yet would index to
 * `undefined` and `translate` would throw on `.replace`. Callers ask first and show the
 * raw code when the answer is no — **visible drift beats a blank caption.**
 */
export function hasMessage(key: string): key is MessageKey {
  return key in ja;
}

export function translate(
  locale: Locale,
  key: MessageKey,
  values: Readonly<Record<string, string | number>> = {},
): string {
  const template = (locale === "ja" ? ja : en)[key];
  return template.replace(/\{(\w+)\}/g, (match, name: string) => String(values[name] ?? match));
}

export function setDocumentLocale(locale: Locale): void {
  if (typeof document !== "undefined") document.documentElement.lang = locale;
}
