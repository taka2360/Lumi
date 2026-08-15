/**
 * クレジットとライセンスの中身。**データだけ。描画は Credits.tsx。**
 *
 * 設計 → docs/licensing.md §6「Phase 0 のクレジット画面に載せるもの」
 *
 * **Phase 0 のクレジットは静的で、Core にも外部にも接続しない**
 * （docs/architecture/ui.md「`credits` を Core に繋がない理由」）。
 * したがって「いま使っているもの」を知らない。**知らないものを省略せず、
 * 適用条件を書いて全部載せる。** クレジットは欠けたときだけ違反になる。
 *
 * 使用中のものに絞るのは Phase 1 の `Provider.attribution()`。
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
  /** 全文をこの画面のどこで読めるか。空なら全文は載せていない。 */
  readonly licenses: readonly LicenseId[];
  /** **どんなときに適用されるか。** Phase 0 は「使用中」を知らないので、必ず書く。 */
  readonly appliesWhen: string;
  readonly source: string;
  /** Lumi の利用者が負う義務。**「知らせる義務」もここに含む。** */
  readonly obligations: readonly string[];
};

export type ProhibitionSet = {
  readonly source: string;
  readonly appliesWhen: string;
  readonly items: readonly string[];
};

/** Lumi 本体。**MIT で、これだけが Lumi の配布物**。 */
export const LUMI = {
  name: "Lumi",
  license: "MIT",
  licenseId: "mit-lumi" as LicenseId,
  description:
    "Lumi 本体（Core / Shell / Stage）は MIT ライセンスです。以下のソフトウェアを利用しています。",
} as const;

/**
 * Lumi が**同梱する** OSS の直接依存。
 *
 * **推移的依存を含む完全な一覧は、何が実際に配布物へ入るかが決まってから生成する**
 * （docs/licensing.md §6「保証しないこと」）。バージョンは package.json /
 * Cargo.toml / pyproject.toml と一致していること（content.test.ts が検査する）。
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
      // sounddevice が同梱するバイナリ。**同梱物なので一緒に載せる。**
      { name: "PortAudio", version: "V19.7.0", license: "MIT" },
    ],
  },
];

/**
 * Lumi が**同梱しない**もの。取得もインストールも**ユーザーの PC の上で**起きる。
 *
 * → docs/licensing.md §2（配布方針）/ §4
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

/** 音源クレジットの書き方の例。**「少し探せばわかる場所」に置く義務への対応。** */
export const CREDIT_EXAMPLES: readonly string[] = ["VOICEVOX:ずんだもん", "VOICEVOX:四国めたん"];

/**
 * 禁止事項。**VOICEVOX の再許諾義務（利用者にも遵守を義務付ける）への対応**であり、
 * ACML 特例条項の「開発元の努力」の一部でもある（docs/licensing.md §5 措置5）。
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
 * ライセンス全文。
 *
 * **GPL-3.0 も載せる。** LGPL-3.0 は本文中で GPL-3.0 を取り込んでおり、
 * LGPL だけでは条件を読めない。
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
 * **推移的依存を含む完全な一覧。** `scripts/generate-oss-notice.mjs` が3つの依存グラフから生成する。
 *
 * 手で書かない。手で書いた一覧は必ず古くなり、**欠けたことに誰も気づけない**。
 * 上の BUNDLED は「読む人が知りたい主要な依存」であり、こちらは「義務としての網羅」。
 */
export const THIRD_PARTY = generated as {
  readonly total: number;
  readonly ecosystems: readonly Ecosystem[];
};

/**
 * 画面の見出しに使う節。**docs/licensing.md §6 の表と1対1**。
 * 節が消えていないことを content.test.ts が検査する。
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
