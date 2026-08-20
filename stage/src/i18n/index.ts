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

const ja = {
  "settings.title": "設定",
  "settings.source.default": "既定",
  "settings.source.file": "設定ファイル",
  "settings.source.env": "環境変数で上書き中",
  "settings.label.inference_device": "推論デバイス",
  "settings.label.llm_model": "LLM モデル",
  "settings.label.stt_model": "音声認識モデル",
  "settings.label.locale": "表示言語",
  "settings.label.tts_speed": "読み上げ速度",
  "settings.choice.auto": "自動（システム設定）",
  "settings.choice.ja": "日本語",
  "settings.choice.en": "English",
  "settings.saved": "次回起動から",
  "settings.applied": "反映済み",
  "settings.refused": "拒否されました",
  "settings.unreadable":
    "設定ファイルを読めませんでした。既定値で動いています（上書きはしないので、手で直せます）",
  "settings.restart": "モデルとデバイスの変更は次回起動から、読み上げ速度はすぐに反映されます",
  "inspector.empty": "まだ1ターンも終わっていない",
  "inspector.interrupted": "途中で止まったターン",
  "boot.title": "Lumi を起動しています…",
  "boot.connecting": "Lumi Core に接続しています…",
  "boot.preparing": "準備しています…",
  "boot.speechModel.fetching": "音声認識モデルを取得しています… {percent}%",
  "boot.engine.fetching": "{engine} を取得しています… {percent}%",
  "boot.engine.starting": "{engine} を起動しています…",
  "boot.speechModel.note": "公式の配布元から取得しています。約 480MB あります。",
  "boot.engine.note": "公式の配布元から取得しています。約 200MB あります。",
  "boot.starting.note": "初回はエンジンが音声モデルを取得するため、数分かかることがあります。",
  "setup.engine.generic": "音声合成エンジン",
  "setup.prompt.tts.title": "音声合成エンジンを取得しますか？",
  "setup.prompt.tts.body.before": "Lumi が声を出すには ",
  "setup.prompt.tts.body.middle": "（LGPL-3.0）が必要です。Lumi には同梱していないため、",
  "setup.prompt.tts.body.strong": "公式の配布元から取得します",
  "setup.prompt.tts.body.after":
    "。取得しないと Lumi は喋れないため、セットアップは完了しません（後からでも取得できます）。",
  "setup.prompt.tts.note.before":
    "これは Lumi が外部へ通信する最初のタイミングです。取得しなければ通信は発生しません。エンジン本体は約 200MB です。",
  "setup.prompt.tts.note.strong":
    "また、エンジンは初回起動時に、エンジン自身が音声モデルを AivisHub から取得します",
  "setup.prompt.tts.note.after": "（この取得は Lumi の検証の対象外です）。",
  "setup.prompt.stt.title": "音声認識モデルを取得しますか？",
  "setup.prompt.stt.body.before": "Lumi が声を聞き取るには ",
  "setup.prompt.stt.body.middle": " のモデル（MIT）が必要です。Lumi には同梱していないため、",
  "setup.prompt.stt.body.strong": "公式の配布元から取得します",
  "setup.prompt.stt.body.after":
    "。取得しないと Lumi は聞き取れないため、セットアップは完了しません（後からでも取得できます）。",
  "setup.prompt.stt.note":
    "取得しなければ通信は発生しません。モデルは約 480MB です。取得したファイルは、あらかじめ決めてある大きさと内容（SHA-256）に一致するかを確認してから使います。",
  "setup.skip": "今は取得しない",
  "setup.install": "取得する",
  "setup.retry": "再試行",
  "setup.quit": "終了",
  "setup.blocked.title": "セットアップは完了していません",
  "setup.blocked.body": "次のものが揃うと、Lumi は起動できます。",
  "setup.blocked.resume": "次回起動時にセットアップを再開できます。",
  "setup.blocked.unknown": "セットアップの状態を確認できませんでした。",
  "status.failure.generic": "取得に失敗しました",
  "status.failure.unknown": "取得に失敗しました（{reason}）",
  "status.failure.origin_not_allowed": "取得元が想定と違いました",
  "status.failure.redirect_not_allowed": "取得中に想定外の配布元へ転送されました",
  "status.failure.redirect_without_location": "取得元の応答が不正でした",
  "status.failure.too_many_redirects": "取得元の転送が多すぎます",
  "status.failure.http_error": "取得元に接続できませんでした",
  "status.failure.size_mismatch": "取得したファイルの大きさが想定と違いました",
  "status.failure.hash_mismatch": "取得したファイルの内容が想定と違いました",
  "status.failure.extract_failed": "展開に失敗しました",
  "status.failure.executable_not_found": "展開結果に実行ファイルがありませんでした",
  "status.failure.tar_not_found": "展開に使う tar が見つかりませんでした",
  "status.failure.network_unreachable": "ネットワークに接続できませんでした",
  "status.failure.disk_error":
    "ファイルの書き込みに失敗しました（空き容量とアクセス権を確認してください）",
  "status.failure.model_incomplete": "取得したモデルのファイルが揃いませんでした",
  "status.failure.unknown_model": "指定されたモデルは取得対象ではありません",
  "status.failure.cancelled": "中断されました",
  "status.failure.unexpected_error": "想定外のエラーが起きました",
  "status.tts.failed": "{engine} を起動できませんでした（入ってはいますが、動いていません）",
  "status.tts.installing": "{engine} を取得中… {percent}%",
  "status.tts.missing": "音声合成エンジンがセットアップされていません（Lumi は喋れません）",
  "status.llm.missing": "Ollama が見つかりません（Lumi は返事ができません）",
  "status.llm.installHint": "ollama.com からインストールしてください",
  "status.llm.modelMissing": "モデル {model} がまだありません",
  "status.llm.stopped":
    "Ollama が起動していません（入ってはいます）。起動してから Lumi を起動し直してください",
  "status.llm.failed": "Ollama が応答していますが、正常に動いていません",
  "status.stt.installing": "音声認識モデルを取得中… {percent}%",
  "status.stt.missing": "音声認識モデルがセットアップされていません（Lumi は聞き取れません）",
  "credits.title": "クレジットとライセンス",
  "credits.license": "ライセンス",
  "credits.bundled.title": "Lumi に同梱しているソフトウェア",
  "credits.bundled.lead": "Lumi のインストーラに含まれるものです。",
  "credits.external.title": "Lumi に同梱していないソフトウェア",
  "credits.external.lead":
    "Lumi はこれらを配布していません。取得もインストールも、あなたの PC の上で行われます。使っていないものも含めて載せています。",
  "credits.appliesWhen": "適用されるとき",
  "credits.source": "入手元",
  "credits.voice.title": "音声のクレジット表記",
  "credits.voice.lead":
    "VOICEVOX の音源を使って音声を公開するときは、次の形でクレジットを表記してください。",
  "credits.voice.acml": "ACML の音声合成モデルではクレジット表記は任意です。",
  "credits.prohibitions.title": "禁止されていること",
  "credits.prohibitions.lead":
    "Lumi で作った音声にも、元になった音源・モデルの規約がそのまま適用されます。",
  "credits.thirdParty.title": "サードパーティの完全な一覧",
  "credits.thirdParty.lead": "推移的な依存を含めて {count} 件。依存グラフから生成しています。",
  "credits.packages": "{count} 件",
  "credits.licenses.title": "ライセンス全文",
  "character.load.status": "VRM を読み込めません ({status}): {path}",
  "character.load.failed": "VRM を読み込めません: {reason}",
  "character.load.invalid": "VRM として読めません: {url}",
} as const;

const en: Record<keyof typeof ja, string> = {
  "settings.title": "Settings",
  "settings.source.default": "Default",
  "settings.source.file": "Settings file",
  "settings.source.env": "Overridden by environment",
  "settings.label.inference_device": "Inference device",
  "settings.label.llm_model": "LLM model",
  "settings.label.stt_model": "Speech recognition model",
  "settings.label.locale": "Display language",
  "settings.label.tts_speed": "Speech speed",
  "settings.choice.auto": "Automatic (system)",
  "settings.choice.ja": "日本語",
  "settings.choice.en": "English",
  "settings.saved": "After restart",
  "settings.applied": "Applied",
  "settings.refused": "Refused",
  "settings.unreadable":
    "The settings file could not be read. Lumi is using defaults and will not overwrite the file, so you can repair it manually.",
  "settings.restart":
    "Model and device changes take effect after restarting; speech speed applies immediately.",
  "inspector.empty": "No turn has finished yet",
  "inspector.interrupted": "Interrupted turn",
  "boot.title": "Starting Lumi…",
  "boot.connecting": "Connecting to Lumi Core…",
  "boot.preparing": "Getting ready…",
  "boot.speechModel.fetching": "Downloading speech recognition model… {percent}%",
  "boot.engine.fetching": "Downloading {engine}… {percent}%",
  "boot.engine.starting": "Starting {engine}…",
  "boot.speechModel.note": "Downloading from the official source (about 480 MB).",
  "boot.engine.note": "Downloading from the official source (about 200 MB).",
  "boot.starting.note":
    "On first launch, the engine may take several minutes to download its voice model.",
  "setup.engine.generic": "speech synthesis engine",
  "setup.prompt.tts.title": "Download the speech synthesis engine?",
  "setup.prompt.tts.body.before": "Lumi needs ",
  "setup.prompt.tts.body.middle":
    " (LGPL-3.0) to speak. It is not bundled with Lumi, so Lumi will ",
  "setup.prompt.tts.body.strong": "download it from the official source",
  "setup.prompt.tts.body.after":
    ". Without it Lumi cannot speak, so setup stays incomplete — you can download it later.",
  "setup.prompt.tts.note.before":
    "This is the first time Lumi will connect to the internet. Declining causes no network traffic. The engine is about 200 MB. ",
  "setup.prompt.tts.note.strong":
    "On first launch, the engine itself also downloads a voice model from AivisHub",
  "setup.prompt.tts.note.after": "; Lumi does not verify that separate download.",
  "setup.prompt.stt.title": "Download the speech recognition model?",
  "setup.prompt.stt.body.before": "Lumi needs the ",
  "setup.prompt.stt.body.middle":
    " model (MIT) to hear you. It is not bundled with Lumi, so Lumi will ",
  "setup.prompt.stt.body.strong": "download it from the official source",
  "setup.prompt.stt.body.after":
    ". Without it Lumi cannot hear you, so setup stays incomplete — you can download it later.",
  "setup.prompt.stt.note":
    "Declining causes no network traffic. The model is about 480 MB. Lumi checks the downloaded file against its predetermined size and SHA-256 digest before using it.",
  "setup.skip": "Not now",
  "setup.install": "Download",
  "setup.retry": "Try again",
  "setup.quit": "Quit",
  "setup.blocked.title": "Setup is not finished",
  "setup.blocked.body": "Lumi can start once the following are in place.",
  "setup.blocked.resume": "Setup will resume the next time you start Lumi.",
  "setup.blocked.unknown": "Could not determine the setup state.",
  "status.failure.generic": "Download failed",
  "status.failure.unknown": "Download failed ({reason})",
  "status.failure.origin_not_allowed": "The download source was not the expected source",
  "status.failure.redirect_not_allowed": "The download was redirected to an unexpected source",
  "status.failure.redirect_without_location": "The download source returned an invalid response",
  "status.failure.too_many_redirects": "The download source redirected too many times",
  "status.failure.http_error": "Could not connect to the download source",
  "status.failure.size_mismatch": "The downloaded file had an unexpected size",
  "status.failure.hash_mismatch": "The downloaded file did not match the expected contents",
  "status.failure.extract_failed": "Could not extract the download",
  "status.failure.executable_not_found": "No executable was found in the extracted files",
  "status.failure.tar_not_found": "The tar utility required for extraction was not found",
  "status.failure.network_unreachable": "Could not connect to the network",
  "status.failure.disk_error": "Could not write the files (check free space and permissions)",
  "status.failure.model_incomplete": "The downloaded model is missing required files",
  "status.failure.unknown_model": "The requested model is not available for download",
  "status.failure.cancelled": "Download cancelled",
  "status.failure.unexpected_error": "An unexpected error occurred",
  "status.tts.failed": "Could not start {engine} (it is installed, but not running)",
  "status.tts.installing": "Downloading {engine}… {percent}%",
  "status.tts.missing": "The speech synthesis engine is not set up (Lumi cannot speak)",
  "status.llm.missing": "Ollama was not found (Lumi cannot reply)",
  "status.llm.installHint": "Install it from ollama.com",
  "status.llm.modelMissing": "Model {model} is not installed yet",
  "status.llm.stopped": "Ollama is installed but not running. Start it, then restart Lumi.",
  "status.llm.failed": "Ollama is responding, but not working correctly",
  "status.stt.installing": "Downloading speech recognition model… {percent}%",
  "status.stt.missing": "The speech recognition model is not set up (Lumi cannot hear you)",
  "credits.title": "Credits and licenses",
  "credits.license": "License",
  "credits.bundled.title": "Software bundled with Lumi",
  "credits.bundled.lead": "These components are included in the Lumi installer.",
  "credits.external.title": "Software not bundled with Lumi",
  "credits.external.lead":
    "Lumi does not distribute these components. Downloads and installation happen on your PC. Components you may not use are also listed.",
  "credits.appliesWhen": "Applies when",
  "credits.source": "Source",
  "credits.voice.title": "Voice attribution",
  "credits.voice.lead":
    "When publishing audio made with a VOICEVOX voice, include attribution in the following form.",
  "credits.voice.acml": "Attribution is optional for voice models licensed under ACML.",
  "credits.prohibitions.title": "Prohibited uses",
  "credits.prohibitions.lead":
    "The terms of the original voice source and model also apply to audio made with Lumi.",
  "credits.thirdParty.title": "Complete third-party list",
  "credits.thirdParty.lead":
    "{count} packages including transitive dependencies, generated from the dependency graph.",
  "credits.packages": "{count} packages",
  "credits.licenses.title": "Full license texts",
  "character.load.status": "Could not load VRM ({status}): {path}",
  "character.load.failed": "Could not load VRM: {reason}",
  "character.load.invalid": "The file is not a readable VRM: {url}",
};

export type MessageKey = keyof typeof ja;

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
