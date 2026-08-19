import type { Locale } from "../i18n";

/** License texts themselves remain the bundled originals; this translates surrounding notices. */
const ENGLISH: Readonly<Record<string, string>> = {
  "Lumi 本体（Core / Shell / Stage）は MIT ライセンスです。以下のソフトウェアを利用しています。":
    "Lumi itself (Core / Shell / Stage) is licensed under MIT and uses the following software.",
  "Lumi Stage（画面）": "Lumi Stage (interface)",
  "Lumi Shell（デスクトップ）": "Lumi Shell (desktop)",
  "Lumi Core（判断と状態）": "Lumi Core (decisions and state)",
  "初回セットアップで取得した場合、または AivisSpeech を自分でインストールしている場合":
    "You downloaded it during first-run setup or installed AivisSpeech yourself",
  "Lumi はこのエンジンを同梱していません。取得は公式の配布元から、あなたの PC の上で行われます。":
    "Lumi does not bundle this engine. It is downloaded from the official source onto your PC.",
  "エンジンは初回起動時に、エンジン自身の判断で音声合成モデル（AivisHub）と言語モデル（HuggingFace）を取得します。":
    "On first launch, the engine independently downloads a voice model from AivisHub and a language model from Hugging Face.",
  "エンジン自身が使っているライブラリのライセンス一覧は、エンジンに同梱されています。":
    "The engine includes the license list for its own libraries.",
  "LGPL-3.0（ENGINE）/ VOICEVOX ソフトウェア利用規約":
    "LGPL-3.0 (ENGINE) / VOICEVOX Software Terms of Use",
  "自分でインストールした VOICEVOX を Lumi が検出して使う場合":
    "Lumi detects and uses a VOICEVOX installation you installed yourself",
  "VOICEVOX を利用したことがわかるクレジット表記が必要です。":
    "Attribution identifying the use of VOICEVOX is required.",
  "音源ごとのクレジット表記が必要です（例: VOICEVOX:ずんだもん）。":
    "Each voice source must be credited (for example, VOICEVOX: Zundamon).",
  "音源の共通規約の禁止事項を守る必要があります（下記）。":
    "You must follow the voice source's common prohibited-use terms listed below.",
  "音声合成モデル（AIVMX）": "Voice synthesis model (AIVMX)",
  "AivisSpeech Engine が取得した既定の音声合成モデルを使う場合（別ライセンスのモデルを自分で追加した場合は、そのモデルのライセンスが適用されます）":
    "You use the default voice model downloaded by AivisSpeech Engine (a separately added model is governed by its own license)",
  "モデルのクレジット表記は任意です（制作者・話者への敬意ある利用をお願いします）。":
    "Model attribution is optional; please use it with respect for its creator and speaker.",
  "禁止事項があります（下記）。": "Prohibited uses apply as listed below.",
  "3Dモデル「光莉 / ひかり」（あわ）": "3D model “Hikari” (Awa)",
  "VRoid Hub 利用条件（モデル登録者が設定）": "VRoid Hub conditions set by the model owner",
  "既定の Content Pack を使う場合（別のモデルに差し替えた場合は、そのモデルの利用条件が適用されます）":
    "You use the default Content Pack (a replacement model is governed by its own terms)",
  "クレジット表記は不要です（2026-08-16 時点の条件）。":
    "Attribution is not required under the terms as of 2026-08-16.",
  "再配布・改変が許諾されているため、Lumi の配布物に含まれています。":
    "It is bundled with Lumi because redistribution and modification are permitted.",
  "ACML 1.0（音声合成モデル）": "ACML 1.0 (voice synthesis model)",
  "ACML の音声合成モデルで音声を作るとき": "You create audio with an ACML voice model",
  "話者本人・原作者・公式関係者であるとの誤解を招く / 騙す利用（ディープフェイク等）":
    "Impersonation or deceptive use that suggests the speaker, original creator, or an official party is involved (including deepfakes)",
  "話者のイメージ・尊厳・品位・社会的評価を傷つける / 貶める利用":
    "Use that harms the speaker's image, dignity, integrity, or social reputation",
  "実在の人物・団体・商品を批判・攻撃・嫌がらせ・誹謗中傷・差別する活動":
    "Criticism, attacks, harassment, defamation, or discrimination targeting real people, groups, or products",
  "人々を騙す目的での虚偽情報・コンテンツの公開 / 流布":
    "Publishing or spreading false information or content intended to deceive people",
  "虚偽・誇大なマーケティング、倫理的に問題のあるビジネス":
    "False or exaggerated marketing and ethically problematic business",
  "特定の政治的立場・政治 / 宗教団体・排他的思想・陰謀論への賛同・支援または反対・批判を呼びかける活動":
    "Campaigning for support of or opposition to political positions, political or religious groups, exclusionary ideologies, or conspiracy theories",
  "反社会的・犯罪目的での利用": "Antisocial or criminal use",
  "VOICEVOX 音源 共通規約": "VOICEVOX Voice Source Common Terms",
  "VOICEVOX の音源で音声を作るとき": "You create audio with a VOICEVOX voice",
  公序良俗に反する利用: "Use contrary to public order or morals",
  "政治活動・宗教活動またはそれらにつながる行為。特定の個人・団体（国家を含む）を非難・批判・応援する目的での利用":
    "Political or religious activities, related conduct, or use intended to condemn, criticize, or support a person or organization (including a state)",
  "情報商材での利用、情報商材の宣伝目的での利用": "Use in, or promotion of, information products",
  "意図的な虚偽情報・誤解を招く内容の作成 / 共有 / 拡散":
    "Creating, sharing, or spreading intentionally false or misleading content",
  "風俗営業（接待飲食等 1〜3号営業）および性風俗関連特殊営業での利用":
    "Use in adult-entertainment businesses or related special businesses",
  "反社会的勢力による利用、および反社会的勢力と協力・関与する者の利用":
    "Use by organized crime groups or people who cooperate or associate with them",
  "Lumi 本体（Core / Shell / Stage）に適用されます。":
    "Applies to Lumi itself (Core / Shell / Stage).",
  "AivisSpeech Engine / VOICEVOX ENGINE に適用されます。":
    "Applies to AivisSpeech Engine / VOICEVOX ENGINE.",
  "LGPL-3.0 が取り込んでいる本文です。": "This is the text incorporated by LGPL-3.0.",
  "AivisSpeech Engine が取得する既定の音声合成モデルに適用されます。":
    "Applies to the default voice model downloaded by AivisSpeech Engine.",
};

export function creditText(locale: Locale, text: string): string {
  return locale === "en" ? (ENGLISH[text] ?? text) : text;
}
