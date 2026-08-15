# ADR-021: Python Core を PyInstaller の onedir で固め、resources として同梱する

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-15 |
| 関連 | [../architecture/setup.md](../architecture/setup.md), [../measurements/phase0.md](../measurements/phase0.md), [../licensing.md](../licensing.md), [ADR-019](ADR-019-tts-engine-distribution.md) |

---

## Decision

Python Core を **PyInstaller の onedir**（展開済みディレクトリ）で固め、
Tauri の `bundle.resources` で `<インストール先>/core/` に置く。

1. **onefile にしない**
2. `externalBin`（実行体1つ）ではなく `resources`（ディレクトリ）で運ぶ。
   Shell は `core/lumi-core.exe` → `lumi-core.exe` の順に探す
3. **`lumi-core.exe --self-check` を持つ。** 固めた実行体が、その PC で本当に動くかを確かめる経路
4. **ASIO 版の PortAudio を同梱しない。** ビルド時に検査し、混ざっていたら**ビルドを失敗させる**
5. インストーラは NSIS 1つ。**署名はしない**（Phase 0 では未取得。SmartScreen の警告が出ることを承知の上）

---

## Reason

### uv 同梱ではなく PyInstaller

| | PyInstaller | uv + Python を同梱 |
|---|---|---|
| 配布サイズ | **26.7 MB**（Core 部分） | uv + CPython + 依存で同程度以上 |
| 起動 | **263 ms** | `uv run` は環境解決が入る（実測 350 ms、初回はさらに遅い） |
| 初回実行時のネットワーク | **不要** | uv は Python を取りに行くことがある。**[ADR-019](ADR-019-tts-engine-distribution.md) の Network-optional 原則に反する** |
| ユーザーから見た姿 | 実行体1つ | Python 環境が丸見え |

**決め手は3つ目。** 「ユーザーが選択するまで外部へ通信しない」は Lumi の原則であり、
起動時に Python を取りに行く可能性のある方式は採れない。

### onefile ではなく onedir

| | onefile | onedir |
|---|---|---|
| 配布サイズ | 13.4 MB | 26.7 MB |
| 起動 | 803 ms | **263 ms** |
| ファイル数 | 1 | 89 |
| **強制終了したとき** | **`%TEMP%` に 21.4 MB の展開先が残る** | **何も残らない** |

**最後の行が決め手。** onefile は起動のたびに `%TEMP%\_MEIxxxxx` へ全体を展開し、
正常終了時にしか片付けない。**Lumi は Job Object による強制終了を
「強制終了に耐える唯一の層」として設計している**（[../interfaces/shell.md](../interfaces/shell.md)）ので、
onefile を選ぶと**設計上の正常経路がゴミを残し続ける**ことになる。

サイズ差 13 MB は、NSIS 圧縮後のインストーラでは縮む（実測 13.1 MB）。

### `--self-check` を持つ理由

**開発環境で通ることは、固めた後に通ることを意味しない。**

ネイティブ拡張（sqlite-vec の `vec0.dll`、PortAudio の DLL）は Python コードと違って
自動では入らず、**入っていなくても import は成功する**。実際に読み込むまで分からない。
Phase 2 で「記憶機能が丸ごと動かない」と気づくより、配布物を作った直後に落とす方が安い。

### ASIO を同梱しない理由

`sounddevice` は ASIO 有り・無しの PortAudio を両方同梱しており、
**PyInstaller のフックが `_sounddevice_data` を丸ごと集めるため、指定しなくても ASIO 版が入る**
（実測。1度これで配布物に混入した）。

ASIO SDK は Steinberg の非 OSS であり再配布条件が別にある。**Core = MIT の境界を汚す。**
`sounddevice` は環境変数 `SD_ENABLE_ASIO` があるときだけ ASIO 版を読むので、
**同梱しなければ機能的な損失はない。**

---

## Alternatives

### A. `externalBin`（Tauri のサイドカー機構）を使う

**利点:** Tauri の標準機構。ターゲットトリプル付きの名前で置かれ、`Command::sidecar` で起動できる
**欠点:** **実行体1つしか運べない。** onedir を選んだ時点で使えない。
onefile なら使えるが、上記のとおり onefile を採らない

### B. Nuitka でコンパイルする

**利点:** 起動が速い。バイトコードが露出しない
**欠点:** ビルドが重く、C コンパイラに依存する。**Phase 0 の問題（サイズ・起動・ネイティブ拡張）は
PyInstaller で解決している**ため、複雑さに見合わない

### C. Core を配布せず、初回起動時に Python 環境を作る

**利点:** インストーラが最小
**欠点:** **初回起動でネットワークが要る。** ADR-019 の原則に反する。オフラインの PC で Lumi が起動しない

---

## Trade-offs

### 受け入れるコスト

- **インストール先に 89 個のファイルが並ぶ**（`core/` 以下）。実行体1つの見た目にはならない
- PyInstaller の bootloader（**GPL-2.0-or-later WITH Bootloader-exception**）が配布物に入る。
  例外条項により、これを使って作った実行体に GPL は伝播しない。**クレジットには載せる**
- **Python のバージョンを配布物に焼き込む。** 更新には再ビルドが要る
- **署名していない。** Windows SmartScreen が警告を出す。Phase 0 の範囲では受け入れる

### 得るもの

- 起動が速く、終了時に何も残さない
- **ネットワーク無しで起動できる**（TTS を取得しない選択をしたユーザーでも Lumi は動く）
- `--self-check` により、**別のマシンで壊れていることを、そのマシンで即座に確認できる**

### 保証しないこと

- **別のマシンで動くことは、まだ確認していない。** 確認したのはビルドしたマシンでの動作だけ
- **ウイルス対策ソフトの誤検知は避けられない。** PyInstaller 製の実行体は誤検知されやすく、
  署名が無い状態ではさらに起きやすい

---

## Consequences

- `pnpm build` が Core のビルドを含むようになる（`pnpm build:core` → `pnpm --filter @lumi/shell build`）
- `find_sidecar` が `core/` を先に見る（[../interfaces/shell.md](../interfaces/shell.md)）
- roadmap 未確定事項 #2（パッケージング方式）が解消する
- **`lumi-core.spec` がビルド時の検査を持つ。** sqlite-vec と PortAudio が入っていること、
  ASIO が入っていないことを、ビルドの成否として表現する
- Phase 1 で numpy（VAD）が入ると配布サイズが増える。**そのとき再測する**
