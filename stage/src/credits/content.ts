/**
 * The content of the credits and licenses. **Data only. Rendering lives in Credits.tsx.**
 *
 * Design → docs/licensing.md §6 "What Phase 0's credits screen shows"
 *
 * **Phase 0's credits are static and connect neither to Core nor externally**
 * (docs/architecture/ui.md "Why `credits` doesn't connect to Core").
 * So it has no way to know "what's actually in use." **Nothing unknown is
 * omitted — everything is listed along with when it applies.** Credits become a
 * violation only when something is missing.
 *
 * Narrowing this down to what's actually in use is Phase 1's `Provider.attribution()`.
 */

import type { MessageKey } from "../i18n";
import acmlText from "./licenses/acml-1.0.txt?raw";
import gplText from "./licenses/gpl-3.0.txt?raw";
import lgplText from "./licenses/lgpl-3.0.txt?raw";
import mitText from "./licenses/mit-lumi.txt?raw";
import generated from "./third-party.generated.json" with { type: "json" };

export type LicenseId = "mit-lumi" | "lgpl-3.0" | "gpl-3.0" | "acml-1.0";

export type LicenseDocument = {
  readonly id: LicenseId;
  /** The license's own name. **Never translated.** */
  readonly title: string;
  readonly noteKey: MessageKey;
  readonly text: string;
};

export type Dependency = {
  readonly name: string;
  readonly version: string;
  readonly license: string;
};

export type BundledComponent = {
  readonly componentKey: MessageKey;
  /** A one-line technology summary. **Not translated** — it is the stack's own names. */
  readonly note: string;
  readonly dependencies: readonly Dependency[];
};

export type ExternalComponent = {
  readonly nameKey: MessageKey;
  readonly licenseKey: MessageKey;
  /** Where on this screen the full text can be read. Empty means the full text isn't included. */
  readonly licenses: readonly LicenseId[];
  /** **When this applies.** Phase 0 doesn't know what's "in use," so this is always filled in. */
  readonly appliesWhenKey: MessageKey;
  /** The upstream URL. **Not translated.** */
  readonly source: string;
  /** Obligations Lumi's user bears. **The "obligation to disclose" is included here too.** */
  readonly obligationKeys: readonly MessageKey[];
};

export type ProhibitionSet = {
  readonly sourceKey: MessageKey;
  readonly appliesWhenKey: MessageKey;
  readonly itemKeys: readonly MessageKey[];
};

/** Lumi itself. **MIT, and this is the only thing that's Lumi's own distributable.** */
export const LUMI = {
  name: "Lumi",
  license: "MIT",
  licenseId: "mit-lumi" as LicenseId,
  descriptionKey: "credits.lumi.description" as MessageKey,
} as const;

/**
 * The direct OSS dependencies Lumi **bundles**.
 *
 * **The complete list, including transitive dependencies, is generated once
 * what actually ships in the distributable is decided**
 * (docs/licensing.md §6 "What this does not guarantee"). Versions must match
 * package.json / Cargo.toml / pyproject.toml (checked by content.test.ts).
 */
export const BUNDLED: readonly BundledComponent[] = [
  {
    componentKey: "credits.component.stage",
    note: "React + TypeScript",
    dependencies: [
      { name: "react", version: "19.2.8", license: "MIT" },
      { name: "react-dom", version: "19.2.8", license: "MIT" },
      { name: "three", version: "0.185.1", license: "MIT" },
      { name: "@pixiv/three-vrm", version: "3.5.5", license: "MIT" },
      { name: "zustand", version: "5.0.15", license: "MIT" },
      { name: "@tauri-apps/api", version: "2.11.1", license: "Apache-2.0 OR MIT" },
    ],
  },
  {
    componentKey: "credits.component.shell",
    note: "Tauri 2 / Rust",
    dependencies: [
      { name: "tauri", version: "2.11", license: "Apache-2.0 OR MIT" },
      { name: "tauri-plugin-log", version: "2.9", license: "Apache-2.0 OR MIT" },
      { name: "tokio", version: "1.53", license: "MIT" },
      { name: "tokio-tungstenite", version: "0.29", license: "MIT" },
      { name: "futures-util", version: "0.3", license: "MIT OR Apache-2.0" },
      { name: "serde", version: "1.0", license: "MIT OR Apache-2.0" },
      { name: "serde_json", version: "1.0", license: "MIT OR Apache-2.0" },
      { name: "log", version: "0.4", license: "MIT OR Apache-2.0" },
      { name: "rand", version: "0.9", license: "MIT OR Apache-2.0" },
      { name: "windows-sys", version: "0.61", license: "MIT OR Apache-2.0" },
    ],
  },
  {
    componentKey: "credits.component.core",
    note: "Python / asyncio",
    dependencies: [
      { name: "websockets", version: "17.0.1", license: "BSD-3-Clause" },
      { name: "structlog", version: "26.1.0", license: "MIT OR Apache-2.0" },
      { name: "sqlite-vec", version: "0.1.9", license: "MIT OR Apache-2.0" },
      { name: "httpx", version: "0.28.1", license: "BSD-3-Clause" },
      { name: "sounddevice", version: "0.5.5", license: "MIT" },
      // The binary sounddevice bundles. **Listed alongside it since it's bundled too.**
      { name: "PortAudio", version: "V19.7.0", license: "MIT" },
    ],
  },
];

/**
 * Things Lumi **doesn't bundle**. Both fetching and installing happen **on the user's own PC**.
 *
 * → docs/licensing.md §2 (distribution policy) / §4
 */
export const EXTERNAL: readonly ExternalComponent[] = [
  {
    nameKey: "credits.external.aivisspeech.name",
    licenseKey: "credits.external.aivisspeech.license",
    licenses: ["lgpl-3.0", "gpl-3.0"],
    appliesWhenKey: "credits.external.aivisspeech.appliesWhen",
    source: "https://github.com/Aivis-Project/AivisSpeech-Engine",
    obligationKeys: [
      "credits.external.aivisspeech.obligation1",
      "credits.external.aivisspeech.obligation2",
      "credits.external.aivisspeech.obligation3",
    ],
  },
  {
    nameKey: "credits.external.voicevox.name",
    licenseKey: "credits.external.voicevox.license",
    licenses: ["lgpl-3.0", "gpl-3.0"],
    appliesWhenKey: "credits.external.voicevox.appliesWhen",
    source: "https://voicevox.hiroshiba.jp/",
    obligationKeys: [
      "credits.external.voicevox.obligation1",
      "credits.external.voicevox.obligation2",
      "credits.external.voicevox.obligation3",
    ],
  },
  {
    nameKey: "credits.external.aivmx.name",
    licenseKey: "credits.external.aivmx.license",
    licenses: ["acml-1.0"],
    appliesWhenKey: "credits.external.aivmx.appliesWhen",
    source: "https://hub.aivis-project.com/",
    obligationKeys: ["credits.external.aivmx.obligation1", "credits.external.aivmx.obligation2"],
  },
  {
    // **表記は義務ではない。それでも出す**（docs/licensing.md §4.5）。
    // 義務の有無と、出すかどうかは別の判断。
    nameKey: "credits.external.model.name",
    licenseKey: "credits.external.model.license",
    // **全文は同梱していない。** 同梱を求められていないため（docs/licensing.md §4.5）。
    // 求められる条件のモデルに差し替えるなら、ここに全文を足す義務が発生する
    licenses: [],
    appliesWhenKey: "credits.external.model.appliesWhen",
    source: "https://hub.vroid.com/characters/7574619046991064867/models/3031358336334644609",
    obligationKeys: ["credits.external.model.obligation1", "credits.external.model.obligation2"],
  },
];

/** Examples of how to write voice-source credit. **Addresses the obligation to place it "somewhere findable with a bit of effort."** */
export const CREDIT_EXAMPLES: readonly string[] = ["VOICEVOX:ずんだもん", "VOICEVOX:四国めたん"];

/**
 * Prohibited uses. **Addresses VOICEVOX's sublicensing obligation (requiring
 * users to comply too)**, and is also part of the "developer's effort" under
 * ACML's special provision (docs/licensing.md §5 measure 5).
 */
export const PROHIBITIONS: readonly ProhibitionSet[] = [
  {
    sourceKey: "credits.prohibition.acml.source",
    appliesWhenKey: "credits.prohibition.acml.appliesWhen",
    itemKeys: [
      "credits.prohibition.acml.item1",
      "credits.prohibition.acml.item2",
      "credits.prohibition.acml.item3",
      "credits.prohibition.acml.item4",
      "credits.prohibition.acml.item5",
      "credits.prohibition.acml.item6",
      "credits.prohibition.acml.item7",
    ],
  },
  {
    sourceKey: "credits.prohibition.voicevox.source",
    appliesWhenKey: "credits.prohibition.voicevox.appliesWhen",
    itemKeys: [
      "credits.prohibition.voicevox.item1",
      "credits.prohibition.voicevox.item2",
      "credits.prohibition.voicevox.item3",
      "credits.prohibition.voicevox.item4",
      "credits.prohibition.voicevox.item5",
      "credits.prohibition.voicevox.item6",
    ],
  },
];

/**
 * The full license texts.
 *
 * **GPL-3.0 is included too.** LGPL-3.0's text incorporates GPL-3.0 by reference,
 * so the terms can't be read from LGPL alone.
 */
export const LICENSES: readonly LicenseDocument[] = [
  {
    id: "mit-lumi",
    title: "MIT License — Lumi",
    noteKey: "credits.license.mit.note",
    text: mitText,
  },
  {
    id: "lgpl-3.0",
    title: "GNU Lesser General Public License Version 3",
    noteKey: "credits.license.lgpl.note",
    text: lgplText,
  },
  {
    id: "gpl-3.0",
    title: "GNU General Public License Version 3",
    noteKey: "credits.license.gpl.note",
    text: gplText,
  },
  {
    id: "acml-1.0",
    title: "Aivis Common Model License (ACML) 1.0",
    noteKey: "credits.license.acml.note",
    text: acmlText,
  },
];

export type Ecosystem = {
  /**
   * Which part of the distributable this covers. **A stable id, not a label** — the
   * wording is in the message catalog, so the generated file carries no display text
   * and the list changes language with the rest of Lumi.
   */
  readonly id: EcosystemId;
  readonly packages: readonly Dependency[];
};

/** The four groups `scripts/generate-oss-notice.mjs` emits. */
export type EcosystemId = "shell" | "core" | "stage" | "undeclared";

/** What each group is called on screen. */
export const ECOSYSTEM_LABEL: Readonly<Record<EcosystemId, MessageKey>> = {
  shell: "credits.ecosystem.shell",
  core: "credits.ecosystem.core",
  stage: "credits.ecosystem.stage",
  undeclared: "credits.ecosystem.undeclared",
};

/**
 * **The complete list, including transitive dependencies.** Generated from all
 * three dependency graphs by `scripts/generate-oss-notice.mjs`.
 *
 * Never written by hand. A hand-written list would inevitably go stale, and
 * **nobody would notice something was missing.** The `BUNDLED` list above is "the
 * main dependencies a reader wants to know about"; this one is "complete coverage as an obligation."
 */
export const THIRD_PARTY = generated as {
  readonly total: number;
  readonly ecosystems: readonly Ecosystem[];
};

/**
 * The sections used for the screen's headings. **A 1:1 match with the table in docs/licensing.md §6**.
 * content.test.ts checks that no section has disappeared.
 */
export const SECTIONS = [
  "lumi",
  "bundled",
  "external",
  "voice",
  "prohibitions",
  "third-party",
  "licenses",
] as const;

export type SectionId = (typeof SECTIONS)[number];
