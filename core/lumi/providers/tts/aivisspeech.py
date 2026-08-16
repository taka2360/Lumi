"""HTTP client for the AivisSpeech / VOICEVOX-compatible engine.

Design → docs/interfaces/provider.md "TTSProvider"

Phase 0 doesn't build the `TTSProvider` protocol yet (that's Phase 1).
What's here is **only the part that talks to the engine**, not yet abstracted.

**Only ever connects to 127.0.0.1.** Making the host swappable would turn this into
"a feature where Lumi sends text to be read aloud to an arbitrary server."
"""

from __future__ import annotations

from typing import Any

import httpx

from lumi import logging as lumi_logging
from lumi.audio.wav import WavError, decode_wav
from lumi.providers.tts.base import SpeechAudio
from lumi.providers.tts.viseme import build_timeline

log = lumi_logging.get_logger(__name__)

#: The engine only ever lives on the same PC as Core. **Not a setting.**
HOST = "127.0.0.1"

#: Wait time for synthesis. **Never wait forever** (fail explicitly if the engine hangs).
SYNTHESIS_TIMEOUT_S = 30.0


class TtsError(RuntimeError):
    """Couldn't speak. **Carries a reason** (never silently goes quiet)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class AivisSpeechClient:
    def __init__(self, port: int) -> None:
        self._base = f"http://{HOST}:{port}"

    async def version(self, probe_seconds: float) -> str | None:
        """Whether it's running. **`None` if not running** (not raised as an exception).

        Used for polling while waiting for startup, so failing to connect isn't abnormal.
        """
        try:
            async with httpx.AsyncClient(timeout=probe_seconds) as client:
                response = await client.get(f"{self._base}/version")
                response.raise_for_status()
                return str(response.json())
        except (httpx.HTTPError, ValueError):
            return None

    async def default_speaker(self) -> int | None:
        """The first style id found.

        **Selecting the speaker is the Content Pack's job**
        (docs/architecture/extension.md §9), and Phase 0 doesn't have a Content Pack
        yet. What's chosen here is provisional and moves to `voice.toml` in Phase 1.
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
        """Turns text into WAV. **Returns the mouth timeline together with it.**

        The engine's contract is to pass `audio_query`'s response straight into `synthesis`.

        **The timeline needs the post-synthesis audio length.** Since AivisSpeech
        doesn't return phoneme lengths from `audio_query` (`adjust_phoneme_length:
        false`), the mora sequence is allocated using the actual audio length →
        `viseme.build_timeline`
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
            # **Never silently play silence.** This happens when garbled input is passed in
            # (observed).
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
