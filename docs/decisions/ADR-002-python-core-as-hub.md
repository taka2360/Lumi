# ADR-002: AI Core を Python 単一プロセスとし、Core をハブとする

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-14 |
| 関連 | [../architecture/core.md](../architecture/core.md) |

---

## Decision

AI Core を **Python / asyncio の単一プロセス**として実装する。
**Core をハブとし、Shell・Stage・Extension はすべて Core のクライアント**とする。

Core は Tauri のサイドカーとして起動され、127.0.0.1 で WebSocket を listen する。

---

## Reason

### 生態系が Python に集中している

| 用途 | ライブラリ | 言語 |
|---|---|---|
| VAD | Silero (ONNX Runtime) | Python が主 |
| STT | faster-whisper (CTranslate2) | **Python のみ** |
| Embedding | Ruri / bge-m3 (ONNX) | Python が主 |
| ベクトル検索 | sqlite-vec | C（Python バインディングあり） |
| LLM クライアント | Ollama | 言語非依存（HTTP） |

**faster-whisper が Python のみ**である点が決定的。TypeScript や Rust で同等品質・同等速度の STT を得るのは難しい。

### 単一プロセスである理由

音声・記憶・Agent を別プロセスに分けると:

- 音声データをプロセス間で運ぶ必要が出る
- **barge-in の critical path が読めなくなる**（[ADR-003](ADR-003-audio-in-core.md)）
- デバッグが難しくなる

単一ユーザーのデスクトップアプリで、プロセス分割によるスケーラビリティの利点は無い。

### ハブである理由

AIRI は eventa IPC（Electron main↔renderer）と WebSocket（server-runtime）の2系統が絡み合っており、「この通信はどっち経由か」が追いにくい。

**Core をハブに固定することで、通信経路が一意に決まる。**

```
Stage ──→ Core ←── Shell
              ↑
         Extension
```

---

## Alternatives

### A. Rust Core

**利点:** Shell と同じ言語。型安全。高速
**欠点:** faster-whisper / Silero / Embedding の Python バインディングを再実装するか、PyO3 で Python を埋め込むことになる。後者なら結局 Python ランタイムが要る

### B. TypeScript Core（AIRI の選択）

**利点:** Stage と同じ言語。Node 生態系
**欠点:**
- STT の選択肢が限られる（whisper.cpp のバインディング等）
- 音声処理を worklet / worker で組むことになり、**barge-in の critical path が長くなる**
- AIRI が実際にこの経路で barge-in を実装できていない

### C. Shell（Rust）に Core を統合

**利点:** プロセスが1つ減る。サイドカーのパッケージング問題が消える
**欠点:** A と同じ。加えて、Shell の責務（OS 特権）と Core の責務（判断）が同一プロセスに同居し、[B3 のセキュリティ境界](../contracts/security-boundaries.md)が消滅する

### D. Core を複数プロセスに分割（音声 / 記憶 / Agent）

**利点:** 障害の隔離
**欠点:** barge-in のレイテンシが読めない。単一ユーザーで得られる利点が無い

---

## Trade-offs

### 受け入れるコスト

| コスト | 内容 | 緩和 |
|---|---|---|
| **パッケージング（R1）** | Python ランタイム + 依存を同梱する。torch 依存なら 1-2GB | **torch を避ける**: faster-whisper=CTranslate2、Silero=ONNX、Embedding=ONNX。Phase 0 で実測 |
| 起動時間 | Python の import は遅い | 遅延ロード。Provider の `load()` を分離 |
| GIL | CPU バウンドな処理で並列性が制限される | 推論は C 拡張内で GIL を解放する。音声コールバックは別スレッド |
| プロセス数が2つ | Shell と Core | サイドカー管理で確実に対応（ゾンビを残さない） |

### R1 が最大のリスク

**torch を避けられるかが Phase 0 の判定ポイント。**

| ライブラリ | torch 依存 |
|---|---|
| faster-whisper (CTranslate2) | **なし** |
| Silero VAD (ONNX Runtime) | **なし** |
| Embedding (ONNX Runtime) | **なし** |
| sqlite-vec | なし |
| Ollama クライアント | なし（HTTP） |
| AivisSpeech / VOICEVOX | なし（HTTP、別プロセス） |

**理論上 torch は不要。** Phase 0 で実測してインストーラサイズを記録する。

---

## Consequences

### Core が「判断の権威」を独占する

Shell は OS 特権を持つが判断しない。Stage は表現するが判断しない。Extension は能力を提供するが判断しない。

**すべての判断が Core にあることで、Invariant 1（Authority）が構造的に守られる。**

### `PlatformShell` と対になる

Shell が差し替え可能（[ADR-001](ADR-001-desktop-shell-tauri.md)）なのに対し、**Core は差し替えない**。Core が Lumi そのものであるため。

### namespace 分離が必要になる

Stage が Shell（Tauri IPC）と Core（WS）の両方と話すため、`shell.*` / `stage.*` / `os.*` / `ext.*` の名前空間を強制分離する（[../architecture/core.md](../architecture/core.md)）。

### 起動シーケンスが決まる

```
Shell 起動 → WS token 生成 → Core をサイドカー起動 → Core が listen
→ Shell が接続・認証 → Core が storage / Provider / Extension を初期化
→ idle Activity を生成 → ready → Stage ウィンドウ作成 → Stage が接続
```

**Phase 0 でこのシーケンス全体を貫通させる。**
