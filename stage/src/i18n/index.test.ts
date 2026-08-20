import { describe, expect, it } from "vitest";

import { resolveConfiguredLocale, resolveLocale, translate } from "./index";

describe("locale resolution", () => {
  it("selects Japanese from a regional tag", () => {
    expect(resolveLocale(["ja-JP"])).toBe("ja");
  });

  it("uses English for unsupported and missing languages", () => {
    expect(resolveLocale(["fr-FR"])).toBe("en");
    expect(resolveLocale([])).toBe("en");
  });

  it("respects the order of supported browser preferences", () => {
    expect(resolveLocale(["en-US", "ja-JP"])).toBe("en");
    expect(resolveLocale(["fr-FR", "ja-JP"])).toBe("ja");
  });
});

describe("configured locale", () => {
  it("honours explicit choices and lets auto use the system locale", () => {
    expect(resolveConfiguredLocale("en", "ja")).toBe("en");
    expect(resolveConfiguredLocale("ja", "en")).toBe("ja");
    expect(resolveConfiguredLocale("auto", "ja")).toBe("ja");
  });
});

it("interpolates translated values", () => {
  expect(translate("en", "status.tts.installing", { engine: "AivisSpeech", percent: 42 })).toBe(
    "Downloading AivisSpeech… 42%",
  );
});
