/**
 * The credits screen renders in the chosen language.
 *
 * **This replaces what `localize.test.ts` guaranteed**, one level closer to the user. That
 * test compared the two halves of a Japanese-keyed lookup table; the wording now lives in
 * the message catalog, where `tsc` already proves every key exists in both languages. What
 * is left to check is the part types cannot: that the screen actually *asks* for the right
 * key in each place, so nothing renders in Japanese while the UI is in English.
 *
 * Licence bodies and the VOICEVOX credit examples are deliberately not translated
 * (docs/architecture/ui.md §6b) and are excluded below.
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { LOCALE_CACHE_KEY } from "../i18n";
import { en } from "../i18n/messages.en";
import { ja } from "../i18n/messages.ja";
import { Credits } from "./Credits";
import { CREDIT_EXAMPLES } from "./content";

let container: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
  root = null;
  container = null;
});

/**
 * The page's text with the licence bodies removed.
 *
 * **The bundled licence texts are never translated** (docs/architecture/ui.md §6b), and the
 * ACML one is written in Japanese — it contains, word for word, phrases that also appear in
 * the prohibition list it governs. Scanning it would report the licence as a translation
 * leak forever.
 */
function visibleWording(node: HTMLElement): string {
  const copy = node.cloneNode(true) as HTMLElement;
  for (const body of copy.querySelectorAll(".credits__text")) {
    body.remove();
  }
  return copy.textContent ?? "";
}

function render(locale: "ja" | "en"): string {
  // The standalone window reads its locale from the cache, having no Core to ask.
  localStorage.setItem(LOCALE_CACHE_KEY, locale);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root?.render(<Credits />));
  return visibleWording(container);
}

/** The credit wording that genuinely differs between the two languages. */
function translatedCreditKeys(): string[] {
  return Object.keys(ja).filter(
    (key) =>
      key.startsWith("credits.") && ja[key as keyof typeof ja] !== en[key as keyof typeof en],
  );
}

describe("the credits screen", () => {
  it("shows Japanese wording in Japanese", () => {
    const text = render("ja");
    expect(text).toContain("AivisSpeech Engine");
    // A sample of prose that has to survive the trip through the catalog.
    expect(text).toContain(ja["credits.external.aivisspeech.obligation1"]);
    expect(text).toContain(ja["credits.prohibition.acml.item1"]);
  });

  it("shows no Japanese wording in English", () => {
    const text = render("en");
    const leaked = translatedCreditKeys().filter((key) =>
      text.includes(ja[key as keyof typeof ja]),
    );
    // **A key asked for in the wrong place is what this catches.** The types already
    // guarantee both languages exist; they cannot tell that the screen used the right one.
    expect(leaked).toEqual([]);
  });

  it("still shows the untranslated attribution examples in English", () => {
    const text = render("en");
    // These are the literal strings a user must reproduce, so they stay as they are.
    for (const example of CREDIT_EXAMPLES) {
      expect(text).toContain(example);
    }
  });

  it("carries the English obligations", () => {
    const text = render("en");
    expect(text).toContain(en["credits.external.aivisspeech.obligation1"]);
    expect(text).toContain(en["credits.prohibition.voicevox.item1"]);
  });
});
