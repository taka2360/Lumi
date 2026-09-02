/**
 * Resolving which language to show. **Presentation only** — Core has no locale
 * (docs/architecture/ui.md §6b).
 *
 * `auto` reads the browser's preference; an explicit `ja` / `en` overrides it. Anything
 * unrecognised, including a missing language tag, resolves to English.
 */

/** Presentation-only locale handling. Core owns decisions and state; this module owns wording. */

export const SUPPORTED_LOCALES = ["ja", "en"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export type LocaleSetting = "auto" | Locale;
export const LOCALE_CACHE_KEY = "lumi.locale";

export function resolveLocale(languages: readonly string[]): Locale {
  for (const language of languages) {
    const primary = language.toLowerCase().split(/[-_]/, 1)[0];
    if (primary === "ja" || primary === "en") return primary;
  }
  return "en";
}

export function browserLocale(): Locale {
  if (typeof navigator === "undefined") return "en";
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  return resolveLocale(languages.filter(Boolean));
}

export function resolveConfiguredLocale(setting: string | null, automatic: Locale): Locale {
  return setting === "ja" || setting === "en" ? setting : automatic;
}

export function cachedLocale(): Locale {
  try {
    if (typeof localStorage !== "undefined") {
      const cached = localStorage.getItem(LOCALE_CACHE_KEY);
      if (cached === "ja" || cached === "en") return cached;
    }
  } catch {
    // Storage is only a startup cache; browser locale remains authoritative for `auto`.
  }
  return browserLocale();
}

export function cacheLocale(locale: Locale): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(LOCALE_CACHE_KEY, locale);
  } catch {
    // A denied cache must never prevent the Core-owned setting from taking effect in memory.
  }
}
