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

import acmlText from "./licenses/acml-1.0.txt?raw";
import gplText from "./licenses/gpl-3.0.txt?raw";
import lgplText from "./licenses/lgpl-3.0.txt?raw";
import mitText from "./licenses/mit-lumi.txt?raw";
import generated from "./third-party.generated.json" with { type: "json" };

export type LicenseId = "mit-lumi" | "lgpl-3.0" | "gpl-3.0" | "acml-1.0";

export type LicenseDocument = {
  readonly id: LicenseId;
  readonly title: string;
  readonly note: string;
  readonly text: string;
};

export type Dependency = {
  readonly name: string;
  readonly version: string;
  readonly license: string;
};

export type BundledComponent = {
  readonly component: string;
  readonly note: string;
  readonly dependencies: readonly Dependency[];
};

export type ExternalComponent = {
  readonly name: string;
  readonly license: string;
  /** Where on this screen the full text can be read. Empty means the full text isn't included. */
  readonly licenses: readonly LicenseId[];
  /** **When this applies.** Phase 0 doesn't know what's "in use," so this is always filled in. */
  readonly appliesWhen: string;
  readonly source: string;
  /** Obligations Lumi's user bears. **The "obligation to disclose" is included here too.** */
  readonly obligations: readonly string[];
};

export type ProhibitionSet = {
  readonly source: string;
  readonly appliesWhen: string;
  readonly items: readonly string[];
};

/** Lumi itself. **MIT, and this is the only thing that's Lumi's own distributable.** */
export const LUMI = {
  name: "Lumi",
  license: "MIT",
  licenseId: "mit-lumi" as LicenseId,
  description:
    "Lumi 本体（Core / Shell / Stage）は MIT ライセンスです。以下のソフトウェアを利用しています。",
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
    component: "Lumi Stage（画面）",
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
    component: "Lumi Shell（デスクトップ）",
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
    component: "Lumi Core（判断と状態）",
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
    name: "AivisSpeech Engine",
    license: "LGPL-3.0",
    licenses: ["lgpl-3.0", "gpl-3.0"],
    appliesWhen:
      "初回セットアップで取得した場合、または AivisSpeech を自分でインストールしている場合",
    source: "https://github.com/Aivis-Project/AivisSpeech-Engine",
    obligations: [
      "Lumi はこのエンジンを同梱していません。取得は公式の配布元から、あなたの PC の上で行われます。",
      "エンジンは初回起動時に、エンジン自身の判断で音声合成モデル（AivisHub）と言語モデル（HuggingFace）を取得します。",
      "エンジン自身が使っているライブラリのライセンス一覧は、エンジンに同梱されています。",
    ],
  },
  {
    name: "VOICEVOX / VOICEVOX ENGINE",
    license: "LGPL-3.0（ENGINE）/ VOICEVOX ソフトウェア利用規約",
    licenses: ["lgpl-3.0", "gpl-3.0"],
    appliesWhen: "自分でインストールした VOICEVOX を Lumi が検出して使う場合",
    source: "https://voicevox.hiroshiba.jp/",
    obligations: [
      "VOICEVOX を利用したことがわかるクレジット表記が必要です。",
      "音源ごとのクレジット表記が必要です（例: VOICEVOX:ずんだもん）。",
      "音源の共通規約の禁止事項を守る必要があります（下記）。",
    ],
  },
  {
    name: "音声合成モデル（AIVMX）",
    license: "Aivis Common Model License (ACML) 1.0",
    licenses: ["acml-1.0"],
    appliesWhen:
      "AivisSpeech Engine が取得した既定の音声合成モデルを使う場合（別ライセンスのモデルを自分で追加した場合は、そのモデルのライセンスが適用されます）",
    source: "https://hub.aivis-project.com/",
    obligations: [
      "モデルのクレジット表記は任意です（制作者・話者への敬意ある利用をお願いします）。",
      "禁止事項があります（下記）。",
    ],
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
    source: "ACML 1.0（音声合成モデル）",
    appliesWhen: "ACML の音声合成モデルで音声を作るとき",
    items: [
      "話者本人・原作者・公式関係者であるとの誤解を招く / 騙す利用（ディープフェイク等）",
      "話者のイメージ・尊厳・品位・社会的評価を傷つける / 貶める利用",
      "実在の人物・団体・商品を批判・攻撃・嫌がらせ・誹謗中傷・差別する活動",
      "人々を騙す目的での虚偽情報・コンテンツの公開 / 流布",
      "虚偽・誇大なマーケティング、倫理的に問題のあるビジネス",
      "特定の政治的立場・政治 / 宗教団体・排他的思想・陰謀論への賛同・支援または反対・批判を呼びかける活動",
      "反社会的・犯罪目的での利用",
    ],
  },
  {
    source: "VOICEVOX 音源 共通規約",
    appliesWhen: "VOICEVOX の音源で音声を作るとき",
    items: [
      "公序良俗に反する利用",
      "政治活動・宗教活動またはそれらにつながる行為。特定の個人・団体（国家を含む）を非難・批判・応援する目的での利用",
      "情報商材での利用、情報商材の宣伝目的での利用",
      "意図的な虚偽情報・誤解を招く内容の作成 / 共有 / 拡散",
      "風俗営業（接待飲食等 1〜3号営業）および性風俗関連特殊営業での利用",
      "反社会的勢力による利用、および反社会的勢力と協力・関与する者の利用",
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
    note: "Lumi 本体（Core / Shell / Stage）に適用されます。",
    text: mitText,
  },
  {
    id: "lgpl-3.0",
    title: "GNU Lesser General Public License Version 3",
    note: "AivisSpeech Engine / VOICEVOX ENGINE に適用されます。",
    text: lgplText,
  },
  {
    id: "gpl-3.0",
    title: "GNU General Public License Version 3",
    note: "LGPL-3.0 が取り込んでいる本文です。",
    text: gplText,
  },
  {
    id: "acml-1.0",
    title: "Aivis Common Model License (ACML) 1.0",
    note: "AivisSpeech Engine が取得する既定の音声合成モデルに適用されます。",
    text: acmlText,
  },
];

export type Ecosystem = {
  readonly name: string;
  readonly packages: readonly Dependency[];
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
