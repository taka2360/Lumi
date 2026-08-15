"""TTSProvider の契約。

型の定義 → docs/interfaces/provider.md「TTSProvider」

## `synthesize` が返すのは音声だけではない〔Phase 1 実装時に確定〕

docs の当初の契約は `-> AudioBuffer` だったが、**リップシンクのタイムラインは
合成の「あと」にしか作れない**（AivisSpeech は `audio_query` で音素長を返さない
→ docs/interfaces/renderer.md）。音声と口のタイムラインは同時に決まるので、
**1つの結果として返す。**

`timeline` が `None` のときは**ビセームを送らない**（口は閉じたまま）。
でたらめな時間で口を動かすより、動かさない方がよい。

## 別プロセス・CPU であることの意味

**LLM に VRAM を全振りできる。** これが TTS の選定理由そのものである（ADR-008）。
`resource_hint()` の `EXTERNAL_PROCESS` はその宣言。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lumi.kernel.cancellation import CancelToken
from lumi.providers.base import Provider
from lumi.providers.tts.viseme import VisemeTimeline


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    """合成結果。WAV のバイト列と、口のタイムライン。"""

    wav: bytes
    #: `None` = 口を動かさない（音素の情報が得られなかった）
    timeline: VisemeTimeline | None


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    """どの声で喋るか。**選定は Content Pack の仕事**（`voice.toml`）。"""

    speaker: int
    name: str = ""


class TTSProvider(Provider, Protocol):
    async def synthesize(
        self, text: str, voice: VoiceConfig, cancel_token: CancelToken
    ) -> SpeechAudio: ...

    def supported_languages(self) -> frozenset[str]:
        """**無い言語では明示的に失敗する。** 黙って英語で読み上げない。"""
        ...
