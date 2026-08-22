# Phase 2 実測値

> **このファイルは設計ではなく観測の記録である。** 数値は環境に依存するので、
> **測った環境を必ず一緒に書く**（[phase0.md](phase0.md) / [phase1.md](phase1.md) と同じ規則）。

関連: [../roadmap.md](../roadmap.md) Phase 2, [../contracts/privacy.md](../contracts/privacy.md), [../decisions/ADR-040-encrypted-sqlite-driver.md](../decisions/ADR-040-encrypted-sqlite-driver.md)

---

## 測定環境

| | |
|---|---|
| OS | Windows 11 Home 26200 |
| Python | 3.12.11（uv で解決） |
| 測定日 | 2026-08-22 |

---

## spike: 暗号化 SQLite × sqlite-vec × FTS5 × PyInstaller〔2026-08-22〕

**Phase 2 の他の全部の前提。** [privacy.md](../contracts/privacy.md) 末尾の「未決」4項目に答えるために回した。
決定は [ADR-040](../decisions/ADR-040-encrypted-sqlite-driver.md)。

### 使ったもの

| | |
|---|---|
| ライブラリ | `apsw-sqlite3mc` 3.53.4.0 |
| SQLite | 3.53.4（SQLite3 Multiple Ciphers 2.4.0） |
| 暗号 | `chacha20`（sqleet 方式。**明示的に固定**） |
| wheel | cp312 win_amd64 / **3.48 MiB**（Windows wheel は**存在する**） |

### 結果

| # | 確認したこと | 結果 |
|---|---|---|
| 1 | 暗号化 DB に sqlite-vec をロードし、KNN が返るか | **返る**（`vec0` 仮想テーブル、distance 0.0） |
| 2 | 暗号化 DB で FTS5 が日本語を引けるか | **引ける** |
| 3 | ディスク上のファイルが平文でないか | **平文でない**（先頭 16 B はランダム。挿入した目印文字列はファイル内に現れない） |
| 4 | 標準 `sqlite3` で開けてしまわないか | **開けない**（`file is not a database`） |
| 5 | 誤った鍵で開けないか | **開けない**（`apsw.NotADBError`） |
| 6 | WAL が使えるか | **使える** |
| 7 | 平文 DB からの移行経路があるか | `ATTACH ... KEY ''` + `INSERT ... SELECT` で**可能**。`sqlite3mc_export` / `sqlcipher_export` 関数は**この配布物には無い** |
| 8 | PyInstaller の配布物に載るか | **載る**（`apsw/__init__.cp312-win_amd64.pyd` 2.73 MiB） |
| 9 | 固めた配布物で `--self-check` が通るか | **通る**（下記） |

### 開く時間（KDF 込み）

| 回 | `open` + `PRAGMA key` + `SELECT count(*)` |
|---|---|
| 1 回目 | 29.1 ms |
| 2 回目 | 21.8 ms |
| 3 回目 | 21.6 ms |

**起動時に1回。** 会話のクリティカルパス（[audio.md](../architecture/audio.md) §7）には乗らない。

### 配布物サイズ（R1）

同一コミットで、依存を足す前と後をそれぞれ `--clean` ビルドして比較した。

| | PyInstaller onedir |
|---|---|
| 追加前 | 271,878,003 B |
| 追加後 | 274,745,418 B |
| **増分** | **+2,867,415 B（+2.73 MiB / +1.05%）** |

**R1 は再燃しない。** 懸念は「torch 依存でインストーラ 1〜2 GB」であり、桁が3つ違う。

### 固めた配布物での `--self-check`

```text
✓ OS secret store: DPAPI (current user) round-trips the key
✓ Encrypted SQLite: chacha20 / APSW 3.53.4.0 / SQLite 3.53.4
✓ sqlite-vec: v0.1.9 / KNN search works in an encrypted DB
✓ FTS5: Japanese search matches in an encrypted DB
```

**dev 環境で通ることは、固めた配布物で通ることを意味しない。**
実際この2項目は、dev では緑で、サイドカーでは赤だった——
拒否された `sqlite3` 接続を閉じ忘れており、Windows では一時ディレクトリを消せずに落ちていた。
**そのために `--self-check` がある。**

### 判定

**[ADR-038](../decisions/ADR-038-privacy-and-data-retention.md) を修正する必要は無い。** 方針どおりに実装できる。
