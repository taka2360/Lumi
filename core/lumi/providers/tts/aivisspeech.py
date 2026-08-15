"""AivisSpeech / VOICEVOX 互換エンジンの HTTP クライアント。

設計 → docs/interfaces/provider.md「TTSProvider」

Phase 0 では `TTSProvider` protocol をまだ作らない（Phase 1）。
ここに在るのは**エンジンと話す部分だけ**で、抽象化はしていない。

**127.0.0.1 にしか繋がない。** ホストを差し替えられるようにすると、
「Lumi が任意のサーバに読み上げテキストを送る機能」になる。
"""

from __future__ import annotations

from typing import Any

import httpx

from lumi import logging as lumi_logging
from lumi.audio.wav import WavError, decode_wav
from lumi.providers.tts.viseme import VisemeTimeline, build_timeline

log = lumi_logging.get_logger(__name__)

#: エンジンは Core と同じ PC の中だけ。**設定にしない。**
HOST = "127.0.0.1"

#: 合成の待ち時間。**無限に待たない**（エンジンが固まったら明示的に失敗させる）。
SYNTHESIS_TIMEOUT_S = 30.0


class TtsError(RuntimeError):
    """喋れなかった。**理由を持つ**（黙って無音にしない）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class SpeechAudio:
    """合成結果。WAV のバイト列と、口のタイムライン。"""

    __slots__ = ("timeline", "wav")

    def __init__(self, wav: bytes, timeline: VisemeTimeline) -> None:
        self.wav = wav
        self.timeline = timeline


class AivisSpeechClient:
    def __init__(self, port: int) -> None:
        self._base = f"http://{HOST}:{port}"

    async def version(self, probe_seconds: float) -> str | None:
        """起動しているか。**起動していなければ `None`**（例外にしない）。

        起動待ちのポーリングに使うので、繋がらないのは異常ではない。
        """
        try:
            async with httpx.AsyncClient(timeout=probe_seconds) as client:
                response = await client.get(f"{self._base}/version")
                response.raise_for_status()
                return str(response.json())
        except (httpx.HTTPError, ValueError):
            return None

    async def default_speaker(self) -> int | None:
        """最初に見つかったスタイル id。

        **話者の選定は Content Pack の仕事**（docs/architecture/extension.md §9）であり、
        Phase 0 にはまだ Content Pack が無い。ここで選ぶのは暫定であって、
        Phase 1 で `voice.toml` に移す。
        """
        try:
            async with httpx.AsyncClient(timeout=SYNTHESIS_TIMEOUT_S) as client:
                response = await client.get(f"{self._base}/speakers")
                response.raise_for_status()
                speakers = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise TtsError("speakers_unavailable", str(error)) from error

        if not isinstance(speakers, list):
            raise TtsError("speakers_malformed")
        for speaker in speakers:
            styles = speaker.get("styles") if isinstance(speaker, dict) else None
            if not isinstance(styles, list):
                continue
            for style in styles:
                if isinstance(style, dict) and isinstance(style.get("id"), int):
                    return int(style["id"])
        return None

    async def synthesize(self, text: str, speaker: int) -> SpeechAudio:
        """テキストを WAV にする。**口のタイムラインも一緒に返す。**

        `audio_query` の応答をそのまま `synthesis` に渡すのがエンジンの契約。

        **タイムラインには合成後の音声の長さが要る。** AivisSpeech は
        `audio_query` で音素長を返さないため（`adjust_phoneme_length: false`）、
        モーラ列を実際の音声の長さで割り振る → `viseme.build_timeline`
        """
        try:
            async with httpx.AsyncClient(timeout=SYNTHESIS_TIMEOUT_S) as client:
                query_response = await client.post(
                    f"{self._base}/audio_query", params={"text": text, "speaker": speaker}
                )
                query_response.raise_for_status()
                query: Any = query_response.json()

                audio_response = await client.post(
                    f"{self._base}/synthesis", params={"speaker": speaker}, json=query
                )
                audio_response.raise_for_status()
                wav = audio_response.content
        except httpx.HTTPError as error:
            raise TtsError("synthesis_failed", str(error)) from error
        except ValueError as error:
            raise TtsError("query_malformed", str(error)) from error

        if not isinstance(query, dict):
            raise TtsError("query_malformed", "audio_query が辞書を返さなかった")

        try:
            audio_seconds = decode_wav(wav).duration_seconds
        except WavError as error:
            raise TtsError("wav_malformed", str(error)) from error

        timeline = build_timeline(query, audio_seconds)
        if not timeline.spans:
            # **黙って無音を再生しない。** 文字化けした入力を渡すとこうなる（実測）。
            raise TtsError("no_moras", "モーラが1つも得られなかった")

        log.info(
            "tts.synthesized",
            chars=len(text),
            bytes=len(wav),
            audio_ms=round(audio_seconds * 1000),
            spans=len(timeline.spans),
            total_ms=timeline.total_ms,
        )
        return SpeechAudio(wav=wav, timeline=timeline)
