"""**同梱されたサイドカーが、その PC で本当に動くか**を確かめる。

```
lumi-core.exe --self-check
```

roadmap Phase 0 検証手順 13（**サイドカーから sqlite-vec がロードできるか**）を、
**配布物そのものに対して**実行できるようにしたもの。

開発環境（`uv run pytest`）で通ることは、**PyInstaller で固めた後に通ることを意味しない。**
ネイティブ拡張（sqlite-vec の `vec0.dll`、PortAudio の DLL）は Python コードと違って
自動では入らず、入っていなくても **import 時には失敗しない**。実際に読み込むまで分からない。

Phase 2 で「記憶機能が丸ごと動かない」と気づくより、**ここで落とす。**
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'✓' if self.ok else '✗'} {self.name}: {self.detail}"


def check_sqlite_vec() -> CheckResult:
    """**ロードして、実際に検索する。** import が通っただけでは何も保証しない。"""
    try:
        import sqlite_vec
    except ImportError as error:
        return CheckResult("sqlite-vec", False, f"import できない: {error}")

    try:
        with sqlite3.connect(":memory:") as db:
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            version = db.execute("select vec_version()").fetchone()[0]
            db.execute("create virtual table v using vec0(embedding float[4])")
            db.execute(
                "insert into v(rowid, embedding) values (1, ?)",
                [sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4])],
            )
            hit = db.execute(
                "select rowid from v where embedding match ? order by distance limit 1",
                [sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4])],
            ).fetchone()
        if hit is None:
            return CheckResult("sqlite-vec", False, "KNN 検索が何も返さない")
    except (sqlite3.Error, AttributeError) as error:
        return CheckResult("sqlite-vec", False, f"{type(error).__name__}: {error}")
    return CheckResult("sqlite-vec", True, f"{version} / KNN 検索が動く")


def check_fts5() -> CheckResult:
    """FTS5 は SQLite のビルド時オプション。**入っていない SQLite が実在する。**"""
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("create virtual table f using fts5(body)")
            db.execute("insert into f(body) values ('こんにちは')")
            found = db.execute("select count(*) from f where f match 'こんにちは'").fetchone()[0]
        if found != 1:
            return CheckResult("FTS5", False, "検索が一致しない")
    except sqlite3.Error as error:
        return CheckResult("FTS5", False, str(error))
    return CheckResult("FTS5", True, f"SQLite {sqlite3.sqlite_version}")


def check_audio() -> CheckResult:
    """PortAudio の DLL が同梱されているか。**デバイスの有無とは別の話。**

    デバイスが1台も無くても、ライブラリが読めていればここは成功とする
    （入力デバイスが無い PC は正常な状態 → docs/architecture/audio.md §8）。
    """
    try:
        from lumi.audio.probe import list_devices
    except OSError as error:
        return CheckResult("PortAudio", False, f"ライブラリを読めない: {error}")

    try:
        devices, host_apis = list_devices()
    except Exception as error:
        return CheckResult("PortAudio", False, f"{type(error).__name__}: {error}")

    inputs = sum(1 for device in devices if device.can_capture)
    outputs = sum(1 for device in devices if device.can_play)
    names = ", ".join(api.name for api in host_apis)
    return CheckResult("PortAudio", True, f"入力 {inputs} / 出力 {outputs} / ホスト API: {names}")


def check_ssl() -> CheckResult:
    """TTS エンジンの取得に要る。**証明書ストアは PyInstaller で落ちやすい。**"""
    try:
        import ssl

        context = ssl.create_default_context()
        stats = context.cert_store_stats()
    except Exception as error:
        return CheckResult("TLS", False, f"{type(error).__name__}: {error}")
    if stats.get("x509_ca", 0) <= 0:
        return CheckResult("TLS", False, "CA 証明書が1つも無い（HTTPS 取得が失敗する）")
    return CheckResult("TLS", True, f"CA 証明書 {stats['x509_ca']} 件")


def check_vad() -> CheckResult:
    """**barge-in の critical path。** ONNX を読んで、実際に1フレーム推論する。

    モデルは faster-whisper の同梱物を借りている（ADR-023 / docs/licensing.md §4.6）ので、
    **パッケージ更新でパスが変われば、ここで落ちる。** これが検知の唯一の手段である。
    """
    try:
        import numpy as np

        from lumi.audio.vad import WINDOW_SAMPLES, SileroVad, VadModelUnavailable
    except ImportError as error:
        return CheckResult("Silero VAD", False, f"import できない: {error}")

    try:
        vad = SileroVad()
        probability = vad.probability(np.zeros(WINDOW_SAMPLES, dtype=np.float32))
    except (VadModelUnavailable, RuntimeError, OSError, ValueError) as error:
        return CheckResult("Silero VAD", False, f"{type(error).__name__}: {error}")
    if not 0.0 <= probability <= 1.0:
        return CheckResult("Silero VAD", False, f"確率が範囲外: {probability}")
    return CheckResult("Silero VAD", True, "ONNX Runtime (CPU) で推論できる")


def check_stt() -> CheckResult:
    """**モデルではなくランタイムを見る。** モデルは実行時取得（ADR-023）。

    `ctranslate2` はネイティブ拡張であり、**入っていなくても import は通らない**。
    ここで落としておかないと「話しかけても無反応」として現れる。
    """
    try:
        import ctranslate2
        import faster_whisper
    except ImportError as error:
        return CheckResult("faster-whisper", False, f"import できない: {error}")
    versions = f"{faster_whisper.__version__} / CTranslate2 {ctranslate2.__version__}"
    return CheckResult("faster-whisper", True, versions)


def check_content() -> CheckResult:
    """**人格が無い Lumi は Lumi ではない。** 同梱 Content Pack が読めるか。"""
    try:
        from lumi import paths
        from lumi.content.pack import ContentPackError, load_character
    except ImportError as error:
        return CheckResult("Content Pack", False, f"import できない: {error}")

    try:
        pack = load_character(paths.default_character_dir())
    except ContentPackError as error:
        return CheckResult("Content Pack", False, str(error))
    return CheckResult("Content Pack", True, f"{pack.name} / {pack.voice.credit.credit_text}")


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    check_sqlite_vec,
    check_fts5,
    check_audio,
    check_vad,
    check_stt,
    check_content,
    check_ssl,
)


def run() -> int:
    """すべて実行して結果を出す。**1つでも落ちたら 1 を返す。**"""
    frozen = getattr(sys, "frozen", False)
    print(f"Lumi Core self-check（{'同梱サイドカー' if frozen else '開発環境'} / {sys.version}）")
    results = [check() for check in CHECKS]
    for result in results:
        print(f"  {result.line()}")
    failed = [result for result in results if not result.ok]
    if failed:
        print(f"\n{len(failed)} 件失敗した。**この配布物は使えない。**")
        return 1
    print("\nすべて成功。")
    return 0
