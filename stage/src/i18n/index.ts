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
  "actions.menu": "アプリ操作",
  "actions.credits": "クレジットとライセンス",
  "actions.help": "使いかた",
  "help.title": "Lumi の使いかた",
  "help.gestures.title": "マウス操作",
  "help.gestures.lead":
    "キャラクターの上での操作です。起動中やセットアップ中の画面でも同じように使えます。",
  "help.gestures.column.gesture": "操作",
  "help.gestures.column.effect": "できること",
  "help.gesture.touch": "左クリック / 左ドラッグ",
  "help.gesture.touch.effect": "キャラクターに触れる（これから作ります）",
  "help.gesture.menu": "右クリック",
  "help.gesture.menu.effect": "操作メニューを開く",
  "help.gesture.scale": "ホイール",
  "help.gesture.scale.effect": "大きさを変える",
  "help.gesture.move": "Alt + 左ドラッグ / 中ドラッグ",
  "help.gesture.move.effect": "位置を変える",
  "help.menu.title": "操作メニュー",
  "help.menu.lead": "右クリックで開きます。メニューの外を左クリックするか、Esc を押すと閉じます。",
  "help.action.settings": "推論に使うデバイスやモデル、表示言語を変える",
  "help.action.inspector": "Lumi の中で何が起きているかを見る（開発用）",
  "help.action.memory": "覚えていることを見て、直す・忘れさせる",
  "help.action.help": "この画面",
  "help.action.credits": "使っているソフトウェアとライセンス",
  "help.action.quit": "Lumi を終了する",
  "help.tray.title": "メニューが見つからないとき",
  "help.tray.body": "タスクトレイの Lumi のアイコンからも、使いかた・クレジット・終了を開けます。",
  "actions.creditsFailed": "クレジット画面を開けませんでした",
  "actions.quit": "終了",
  "actions.settings": "設定",
  "actions.inspector": "インスペクター",
  "actions.memory": "記憶",
  "actions.openFailed": "画面を開けませんでした",
  "panel.connected": "接続中",
  "panel.disconnected": "Lumi Core に接続していません",
  "settings.waiting": "Lumi Core から設定が届くのを待っています…",
  "inspector.waiting": "まだ何も起きていません",
  "mic.open": "マイクは開いています",
  "mic.muted": "マイクはミュート中",
  "mic.mute": "ミュートする",
  "mic.unmute": "ミュートを解除する",
  "mic.unavailable": "マイクは使えません",
  "mic.failed": "マイクを切り替えられませんでした",
  "memory.title": "記憶",
  "memory.search": "記憶を検索",
  "memory.searchPlaceholder": "覚えている文を探す",
  "memory.includeHistory": "以前の記憶も表示する",
  "memory.empty": "まだ何も覚えていません",
  "memory.pending":
    "まだ何も覚えていません。会話 {count} 行はこれから読みます（静かになってから読むので、すぐには増えません）",
  "memory.disconnected": "Lumi Core に接続していないので、覚えていることを読み出せません",
  "memory.noMatch": "一致する記憶はありません",
  "memory.count": "{shown} / {total} 件",
  "memory.more": "もっと見る",
  "memory.superseded": "更新済み",
  "memory.archived": "薄れている",
  "memory.salience": "強さ",
  "memory.edit": "直す",
  "memory.save": "保存",
  "memory.cancel": "やめる",
  "memory.confirm": "これで合っている",
  "memory.forget": "忘れさせる",
  "memory.forgetConfirm": "この記憶を削除します。元に戻せません。",
  "memory.failed": "できませんでした: {reason}",
  "memory.export": "書き出す",
  "memory.exportWarning": "書き出したファイルは暗号化されません。誰でも読めます。",
  "memory.exported": "書き出しました: {path}（{count} 件）",
  "memory.eraseAll": "全部消す",
  "memory.erasePreview": "消えるもの",
  "memory.eraseRow": "{target}: {count} 件",
  "memory.eraseConfirm": "本当に全部消しますか。元に戻せません。",
  "memory.erased": "全部消しました",
  "memory.target.memory_records": "記憶",
  "memory.target.episodes": "会話の記録",
  "memory.target.domain_events": "イベント履歴",
  "memory.target.audit_log": "監査ログ",
  "memory.mode.user_confirmed": "本人が確認した",
  "memory.mode.user_stated": "本人が言った",
  "memory.mode.inferred": "会話から推測した",
  "memory.mode.self_generated": "Lumi の推測",
  "memory.mode.external": "外部から来た",
  "memory.trust.tainted": "外部由来（未確認）",
  "settings.title": "設定",
  "settings.source.default": "既定",
  "settings.source.file": "設定ファイル",
  "settings.source.env": "環境変数で上書き中",
  "settings.label.inference_device": "推論デバイス",
  "settings.label.llm_model": "LLM モデル",
  "settings.label.stt_model": "音声認識モデル",
  "settings.label.locale": "表示言語",
  "settings.label.tts_speed": "読み上げ速度",
  "settings.label.tts_volume": "音量",
  "settings.choice.auto": "自動（システム設定）",
  "settings.choice.ja": "日本語",
  "settings.choice.en": "English",
  "settings.saved": "次回起動から",
  "settings.applied": "反映済み",
  "settings.refused": "拒否されました",
  "settings.unreadable":
    "設定ファイルを読めませんでした。既定値で動いています（上書きはしないので、手で直せます）",
  "settings.restart":
    "モデルとデバイスの変更は次回起動から、読み上げ速度と音量は次のターンから反映されます（音量 100% はキャラクター本来の音量です）",
  "inspector.title": "インスペクター",
  "inspector.empty": "まだ1ターンも終わっていない",
  "inspector.interrupted": "途中で止まったターン",
  "inspector.discarded": "捨てた先読み",
  "boot.title": "Lumi を起動しています…",
  "boot.connecting": "Lumi Core に接続しています…",
  "boot.preparing": "準備しています…",
  "boot.speechModel.fetching": "音声認識モデルを取得しています… {percent}%",
  "boot.speechModel.starting": "音声認識モデルを読み込んでいます…",
  "boot.llmModel.fetching": "{model} をダウンロード中… {percent}%",
  "boot.llmModel.bytes": "{completed} / {total}",
  "boot.llmModel.note": "Ollamaを通してAIモデルをダウンロードしています。",
  "boot.engine.fetching": "{engine} を取得しています… {percent}%",
  "boot.engine.starting": "{engine} を起動しています…",
  "boot.speechModel.note": "公式の配布元から取得しています。約 480MB あります。",
  "boot.speechModel.starting.note": "初回はモデルの読み込みに時間がかかることがあります。",
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
  "setup.prompt.model.title": "推奨AIモデルをダウンロードしますか？",
  "setup.prompt.model.chooseTitle": "使用するAIモデルを選択",
  "setup.prompt.model.body.before": "Lumi はローカルでの会話に ",
  "setup.prompt.model.body.after": " を推奨します。",
  "setup.prompt.model.chooseBody":
    "Ollamaで利用できるモデルを選択してください。PCにあるモデルは追加ダウンロードなしで使用できます。",
  "setup.prompt.model.downloadNote":
    "Ollamaを通して約 {size} をダウンロードします。通信量、ディスク容量、時間がかかります。",
  "setup.prompt.model.downloadNamed": "{model}（約 {size}）をダウンロード",
  "setup.prompt.model.selectNamed": "{model}（約 {size}・ローカル）を使用",
  "setup.prompt.model.choose": "別のモデルを選ぶ",
  "setup.prompt.model.back": "推奨モデルに戻る",
  "setup.model.gb": "GB",
  "setup.model.mb": "MB",
  "setup.prompt.all.title": "必要なものをまとめて取得しますか",
  "setup.prompt.all.body":
    "Lumi が話し、聞き、覚えるために、合計 {size} をダウンロードします。取得先はいずれも公式の配布元で、ダウンロードするまで外部通信は行いません。",
  "setup.prompt.all.note": "個別に選ぶこともできます。あとから設定画面でも取得できます。",
  "setup.prompt.all.install": "まとめて取得する（{size}）",
  "setup.prompt.all.individually": "個別に選ぶ",
  "setup.prompt.embedding.title": "記憶の検索モデルを取得しますか",
  "setup.prompt.embedding.body.before": "覚えたことを意味で探すために、",
  "setup.prompt.embedding.body.after": "（約 196 MB）をダウンロードします。",
  "setup.prompt.embedding.note":
    "取得しなくても Lumi は動きます。その場合、記憶の検索は語句と新しさだけになります（あとから取得できます）。",
  "status.embedding.missing": "記憶の検索モデルが未取得です（意味検索は使えません）",
  "status.embedding.failed": "記憶の検索モデルを取得できませんでした",
  "boot.embedding.fetching": "記憶の検索モデルを取得しています… {percent}%",
  "setup.skip": "今は取得しない",
  "setup.install": "取得する",
  "setup.retry": "再試行",
  "setup.quit": "終了",
  "setup.ollama.missing.title": "Ollama が見つかりません",
  "setup.ollama.missing.body": "Ollama をインストールしてください。",
  "setup.ollama.why.strong": "Ollama は、AI モデルを PC 上で動かすために使用します。",
  "setup.ollama.why.detail": "Lumi は Ollama を通して、PC 上のローカル言語モデルを実行します。",
  "setup.ollama.autoCheck": "Lumi はバックグラウンドで自動的に確認します。",
  "setup.ollama.openSite": "公式サイトを開く",
  "setup.ollama.actionFailed": "公式サイトを開けませんでした。",
  "setup.ollama.stopped.title": "Ollama を起動してください",
  "setup.ollama.stopped.body":
    "Ollama はインストールされていますが、ローカル API が応答していません。Ollama を起動してください。",
  "setup.ollama.detected.title": "Ollama を検出しました",
  "setup.ollama.checkingModel": "モデルを確認しています…",
  "setup.ollama.starting.title": "Ollama のセットアップを完了しています…",
  "setup.ollama.starting.body": "Ollama の起動を待っています。",
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
  "status.failure.ollama_pull_http_error": "Ollamaがモデルの取得を拒否しました",
  "status.failure.ollama_pull_invalid_response": "Ollamaから不正な応答が返りました",
  "status.failure.ollama_pull_failed": "AIモデルのダウンロードに失敗しました",
  "status.failure.ollama_pull_incomplete": "AIモデルのダウンロードが途中で終了しました",
  "status.failure.ollama_pull_unreachable": "モデル取得中にOllamaへ接続できなくなりました",
  "status.failure.model_selection_unavailable": "選択したモデルを適用できませんでした",
  "status.failure.settings_save_failed": "モデル設定を保存できませんでした",
  "status.tts.failed": "{engine} を起動できませんでした（入ってはいますが、動いていません）",
  "status.tts.installing": "{engine} を取得中… {percent}%",
  "status.tts.missing": "音声合成エンジンがセットアップされていません（Lumi は喋れません）",
  "status.llm.missing": "Ollama が見つかりません（Lumi は返事ができません）",
  "status.llm.installHint": "ollama.com からインストールしてください",
  "status.llm.modelMissing":
    "モデル {model} がまだありません（Lumiのセットアップから取得できます）",
  "status.llm.stopped":
    "Ollama が起動していません（入ってはいます）。起動してから Lumi を起動し直してください",
  "status.llm.failed": "Ollama が応答していますが、正常に動いていません",
  "status.stt.installing": "音声認識モデルを取得中… {percent}%",
  "status.stt.missing": "音声認識モデルがセットアップされていません（Lumi は聞き取れません）",
  "status.stt.failed": "音声認識モデルを読み込めませんでした（取得済みですが、使用できません）",
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
  "character.model.model_not_in_pack": "Content Pack にモデルが含まれていません",
  "character.model.pack_unreadable": "Content Pack を読み込めません",
  "character.load.status": "VRM を読み込めません ({status}): {path}",
  "character.load.failed": "VRM を読み込めません: {reason}",
  "character.load.invalid": "VRM として読めません: {url}",
} as const;

const en: Record<keyof typeof ja, string> = {
  "actions.menu": "Application actions",
  "actions.credits": "Credits and licenses",
  "actions.help": "How to use Lumi",
  "help.title": "How to use Lumi",
  "help.gestures.title": "Mouse",
  "help.gestures.lead": "These work on the character, and on the loading and setup screens too.",
  "help.gestures.column.gesture": "Gesture",
  "help.gestures.column.effect": "What it does",
  "help.gesture.touch": "Left click / drag",
  "help.gesture.touch.effect": "Touch the character (not built yet)",
  "help.gesture.menu": "Right click",
  "help.gesture.menu.effect": "Open the action menu",
  "help.gesture.scale": "Wheel",
  "help.gesture.scale.effect": "Change the size",
  "help.gesture.move": "Alt + left drag / middle drag",
  "help.gesture.move.effect": "Move Lumi",
  "help.menu.title": "The action menu",
  "help.menu.lead": "Right click to open it. Left click outside it, or press Esc, to close it.",
  "help.action.settings": "Change the inference device, the models and the display language",
  "help.action.inspector": "See what is going on inside Lumi (for development)",
  "help.action.memory": "Read what Lumi remembers, correct it, or make it forget",
  "help.action.help": "This page",
  "help.action.credits": "The software Lumi is built on, and its licenses",
  "help.action.quit": "Quit Lumi",
  "help.tray.title": "If you cannot find the menu",
  "help.tray.body": "Lumi's icon in the notification area opens this guide, the credits, and quit.",
  "actions.creditsFailed": "Could not open the credits window",
  "actions.quit": "Quit",
  "actions.settings": "Settings",
  "actions.inspector": "Inspector",
  "actions.memory": "Memory",
  "actions.openFailed": "Could not open the window",
  "panel.connected": "Connected",
  "panel.disconnected": "Not connected to Lumi Core",
  "settings.waiting": "Waiting for settings from Lumi Core…",
  "inspector.waiting": "Nothing has happened yet",
  "mic.open": "The microphone is open",
  "mic.muted": "The microphone is muted",
  "mic.mute": "Mute the microphone",
  "mic.unmute": "Unmute the microphone",
  "mic.unavailable": "No microphone is available",
  "mic.failed": "Could not change the microphone",
  "memory.title": "Memory",
  "memory.search": "Search memories",
  "memory.searchPlaceholder": "Find a remembered sentence",
  "memory.includeHistory": "Show what was superseded",
  "memory.empty": "Nothing has been remembered yet",
  "memory.pending":
    "Nothing has been remembered yet. {count} lines of conversation are still to be read — that happens once things go quiet, so this will not fill up straight away.",
  "memory.disconnected": "Not connected to Lumi Core, so what it remembers cannot be read",
  "memory.noMatch": "No memory matches",
  "memory.count": "{shown} of {total}",
  "memory.more": "Show more",
  "memory.superseded": "Superseded",
  "memory.archived": "Faded",
  "memory.salience": "Salience",
  "memory.edit": "Correct",
  "memory.save": "Save",
  "memory.cancel": "Cancel",
  "memory.confirm": "This is right",
  "memory.forget": "Forget this",
  "memory.forgetConfirm": "This deletes the memory. It cannot be undone.",
  "memory.failed": "Could not do that: {reason}",
  "memory.export": "Export",
  "memory.exportWarning": "The exported file is not encrypted. Anyone who opens it can read it.",
  "memory.exported": "Exported to {path} ({count} memories)",
  "memory.eraseAll": "Erase everything",
  "memory.erasePreview": "What will be erased",
  "memory.eraseRow": "{target}: {count}",
  "memory.eraseConfirm": "Erase everything? This cannot be undone.",
  "memory.erased": "Everything was erased",
  "memory.target.memory_records": "Memories",
  "memory.target.episodes": "Conversations",
  "memory.target.domain_events": "Event history",
  "memory.target.audit_log": "Audit log",
  "memory.mode.user_confirmed": "You confirmed this",
  "memory.mode.user_stated": "You said this",
  "memory.mode.inferred": "Inferred from the conversation",
  "memory.mode.self_generated": "Lumi's own guess",
  "memory.mode.external": "Came from outside",
  "memory.trust.tainted": "From outside, unconfirmed",
  "settings.title": "Settings",
  "settings.source.default": "Default",
  "settings.source.file": "Settings file",
  "settings.source.env": "Overridden by environment",
  "settings.label.inference_device": "Inference device",
  "settings.label.llm_model": "LLM model",
  "settings.label.stt_model": "Speech recognition model",
  "settings.label.locale": "Display language",
  "settings.label.tts_speed": "Speech speed",
  "settings.label.tts_volume": "Volume",
  "settings.choice.auto": "Automatic (system)",
  "settings.choice.ja": "日本語",
  "settings.choice.en": "English",
  "settings.saved": "After restart",
  "settings.applied": "Applied",
  "settings.refused": "Refused",
  "settings.unreadable":
    "The settings file could not be read. Lumi is using defaults and will not overwrite the file, so you can repair it manually.",
  "settings.restart":
    "Model and device changes take effect after restarting; speech speed and volume apply from the next turn (100% volume is the character's own level).",
  "inspector.title": "Inspector",
  "inspector.empty": "No turn has finished yet",
  "inspector.interrupted": "Interrupted turn",
  "inspector.discarded": "Discarded speculations",
  "boot.title": "Starting Lumi…",
  "boot.connecting": "Connecting to Lumi Core…",
  "boot.preparing": "Getting ready…",
  "boot.speechModel.fetching": "Downloading speech recognition model… {percent}%",
  "boot.speechModel.starting": "Loading speech recognition model…",
  "boot.llmModel.fetching": "Downloading {model}… {percent}%",
  "boot.llmModel.bytes": "{completed} / {total}",
  "boot.llmModel.note": "Downloading the AI model through Ollama.",
  "boot.engine.fetching": "Downloading {engine}… {percent}%",
  "boot.engine.starting": "Starting {engine}…",
  "boot.speechModel.note": "Downloading from the official source (about 480 MB).",
  "boot.speechModel.starting.note": "The model may take a while to load the first time.",
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
  "setup.prompt.model.title": "Download the recommended AI model?",
  "setup.prompt.model.chooseTitle": "Choose an AI model",
  "setup.prompt.model.body.before": "Lumi recommends ",
  "setup.prompt.model.body.after": " for local conversations.",
  "setup.prompt.model.chooseBody":
    "Choose a model available to Ollama. Models already on this PC can be used without another download.",
  "setup.prompt.model.downloadNote":
    "This downloads about {size} through Ollama and will use network bandwidth, disk space, and time.",
  "setup.prompt.model.downloadNamed": "Download {model} (about {size})",
  "setup.prompt.model.selectNamed": "Use {model} (about {size}, local)",
  "setup.prompt.model.choose": "Choose another model",
  "setup.prompt.model.back": "Back to recommended model",
  "setup.model.gb": "GB",
  "setup.model.mb": "MB",
  "setup.prompt.all.title": "Fetch everything Lumi needs?",
  "setup.prompt.all.body":
    "Downloading {size} in total, so Lumi can speak, listen and remember. Every download comes from the official distributor, and nothing is fetched until you say so.",
  "setup.prompt.all.note":
    "You can also choose them one at a time. Anything skipped can be fetched later from settings.",
  "setup.prompt.all.install": "Fetch all of it ({size})",
  "setup.prompt.all.individually": "Choose one at a time",
  "setup.prompt.embedding.title": "Fetch the memory search model?",
  "setup.prompt.embedding.body.before": "To search what Lumi remembers by meaning, ",
  "setup.prompt.embedding.body.after": " (about 196 MB) will be downloaded.",
  "setup.prompt.embedding.note":
    "Lumi works without it. Memory search then falls back to keywords and recency, and you can fetch it later.",
  "status.embedding.missing": "The memory search model has not been fetched (no semantic search)",
  "status.embedding.failed": "The memory search model could not be fetched",
  "boot.embedding.fetching": "Downloading the memory search model… {percent}%",
  "setup.skip": "Not now",
  "setup.install": "Download",
  "setup.retry": "Try again",
  "setup.quit": "Quit",
  "setup.ollama.missing.title": "Ollama was not found",
  "setup.ollama.missing.body": "Install Ollama to continue.",
  "setup.ollama.why.strong": "Ollama runs AI models on your PC.",
  "setup.ollama.why.detail": "Lumi uses Ollama to run a local language model on this PC.",
  "setup.ollama.autoCheck": "Lumi checks again automatically in the background.",
  "setup.ollama.openSite": "Open official site",
  "setup.ollama.actionFailed": "Could not open the official Ollama site.",
  "setup.ollama.stopped.title": "Start Ollama",
  "setup.ollama.stopped.body":
    "Ollama is installed, but its local API is not responding. Start Ollama to continue.",
  "setup.ollama.detected.title": "Ollama detected",
  "setup.ollama.checkingModel": "Checking the model…",
  "setup.ollama.starting.title": "Finishing Ollama setup…",
  "setup.ollama.starting.body": "Waiting for Ollama to start.",
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
  "status.failure.ollama_pull_http_error": "Ollama refused the model download",
  "status.failure.ollama_pull_invalid_response": "Ollama returned an invalid response",
  "status.failure.ollama_pull_failed": "The AI model download failed",
  "status.failure.ollama_pull_incomplete": "The AI model download ended before completion",
  "status.failure.ollama_pull_unreachable":
    "Lumi lost its connection to Ollama during the model download",
  "status.failure.model_selection_unavailable": "Could not apply the selected model",
  "status.failure.settings_save_failed": "Could not save the selected model setting",
  "status.tts.failed": "Could not start {engine} (it is installed, but not running)",
  "status.tts.installing": "Downloading {engine}… {percent}%",
  "status.tts.missing": "The speech synthesis engine is not set up (Lumi cannot speak)",
  "status.llm.missing": "Ollama was not found (Lumi cannot reply)",
  "status.llm.installHint": "Install it from ollama.com",
  "status.llm.modelMissing":
    "Model {model} is not installed yet (Lumi can download it during setup)",
  "status.llm.stopped": "Ollama is installed but not running. Start it, then restart Lumi.",
  "status.llm.failed": "Ollama is responding, but not working correctly",
  "status.stt.installing": "Downloading speech recognition model… {percent}%",
  "status.stt.missing": "The speech recognition model is not set up (Lumi cannot hear you)",
  "status.stt.failed":
    "Could not load the speech recognition model (it is downloaded, but not usable)",
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
  "character.model.model_not_in_pack": "This Content Pack ships no model",
  "character.model.pack_unreadable": "The Content Pack could not be read",
  "character.load.status": "Could not load VRM ({status}): {path}",
  "character.load.failed": "Could not load VRM: {reason}",
  "character.load.invalid": "The file is not a readable VRM: {url}",
};

export type MessageKey = keyof typeof ja;

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
