# ADR-040: 暗号化 SQLite の実装 — APSW + SQLite3 Multiple Ciphers に移り、標準の `sqlite3` を Core から外す

| | |
|---|---|
| Status | Accepted |
| Date | 2026-08-22 |
| Related | [ADR-038](ADR-038-privacy-and-data-retention.md)（この決定の前提）, [../contracts/privacy.md](../contracts/privacy.md), [../architecture/memory.md](../architecture/memory.md), [../licensing.md](../licensing.md), [../measurements/phase2.md](../measurements/phase2.md), [../roadmap.md](../roadmap.md) Phase 2a |

## Context

[ADR-038](ADR-038-privacy-and-data-retention.md) は「**会話由来のデータを含む DB は保存時に暗号化する**」と決めたが、
**それをどのライブラリで実現するかは決めていない。** そして [privacy.md](../contracts/privacy.md) 末尾は、
この組み合わせが未検証であることを明示していた。

| 確認すること |
|---|
| 暗号化ビルドで拡張のロード（sqlite-vec）が動くか |
| Windows の wheel があるか |
| PyInstaller の配布物に載るか。サイズ増（R1） |
| 既存の平文イベント DB からの移行経路 |

**ここが通らなければ ADR-038 を修正する ADR が要る**——というのが Phase 2 の入口の条件だった。
Phase 2a の最初に spike を回して確認した。**通った。**

問題の本質は、**Python 標準ライブラリの `sqlite3` は暗号化された DB を開けない**ことである。
暗号化は SQLite 本体のビルドオプションではなく、ページの読み書きに介入する別実装の SQLite が要る。
つまりこれは「暗号化フラグを足す」話ではなく、**Core が使う SQLite ライブラリそのものを差し替える**話である。

## Decision

### 1. `apsw-sqlite3mc` を採る

**APSW（Another Python SQLite Wrapper）を、SQLite3 Multiple Ciphers 込みでビルドした配布物**を使う。

| | |
|---|---|
| PyPI | `apsw-sqlite3mc` 3.53.4.0（SQLite 3.53.4 / SQLite3 Multiple Ciphers 2.4.0） |
| ライセンス | APSW = zlib 型（SPDX: `any-OSI`）/ SQLite3MC = MIT / SQLite = Public Domain |
| Windows wheel | **ある**（cp312 win_amd64、3.48 MiB） |

**Core = MIT の境界を侵さない。** GPL / AGPL は含まれない。

### 2. 暗号は `chacha20` を明示的に固定する

SQLite3MC の既定も `chacha20`（sqleet 方式）だが、**既定に依存しない。**
ライブラリ更新で既定が変われば、**既存の DB がその日から開けなくなる。**

```
PRAGMA cipher = 'chacha20';
PRAGMA key    = '<64 hex chars>';
```

鍵は `PRAGMA key` の**パスフレーズ形式**で渡す（KDF が効く）。渡す値自体が
`secrets.token_bytes(32)` の hex なので、KDF は強度のためではなく形式のためである。

### 3. `lumi.storage` の外に `apsw` を出さない

`import apsw` してよいのは `lumi/storage/` と、そこを検証する selfcheck / テストだけ。
**他のモジュールは `Database` 越しにしか DB を触らない。** 将来ライブラリを替えるとき、
差分が1ディレクトリに収まる。

### 4. オンディスクの DB は、鍵なしでは開けない

`Database.open(path)` は `":memory:"` **以外**では鍵を必須にする。**平文で開く引数を用意しない。**

> 「今回だけ平文で」は、例外が既定になる経路そのものである。

インメモリ DB が鍵を取らないのは、**ディスクに書かれないから**である。

### 5. 標準 `sqlite3` は「暗号化されていないことの証明」にだけ使う

Core は自分の DB を標準 `sqlite3` で開かない。ただし selfcheck とテストでは、
**標準 `sqlite3` で開けないことを確認する**ために使う。開けたら平文に落ちている。

## Reason

### 代替は Windows で成立しなかった

**SQLCipher（`sqlcipher3-binary`）には Windows wheel が無い**（manylinux のみ）。
自前ビルドは OpenSSL のビルドを配布パイプラインに持ち込むことになり、
**インストーラサイズ（R1）と署名の両方に効く。** 採らない。

### 列単位の暗号化では成立しない

「DB は平文のまま、本文カラムだけ暗号化する」は一見安く見えるが、**FTS5 と sqlite-vec が死ぬ。**
全文検索インデックスは平文の語を持たなければ引けず、ベクトルは平文の float 列でなければ探索できない。
**暗号化を回避した部分が、そのまま検索できる平文の会話ログになる。**

### spike で確認したこと〔2026-08-22 実測〕

数値と手順は [measurements/phase2.md](../measurements/phase2.md) が持つ。結果だけ:

| 確認 | 結果 |
|---|---|
| 暗号化 DB で sqlite-vec がロードでき、KNN が返る | **通る** |
| 暗号化 DB で FTS5 が日本語を引ける | **通る** |
| ディスク上のファイルが平文でない（標準 `sqlite3` が開けない） | **通る** |
| 誤った鍵で開けない | **通る**（`NotADBError`） |
| PyInstaller の配布物に載り、`--self-check` が通る | **通る** |
| 配布物のサイズ増 | **+2.73 MiB** |
| 平文 DB からの移行 | `ATTACH ... KEY ''` + `INSERT SELECT` で可能。**ただし今は不要**（下記） |

### 移行経路は「無い」が正しい

privacy.md が挙げていた「既存の平文イベント DB からの移行」は、**移行対象が存在しない。**
Phase 1 のイベント DB は `":memory:"` であり（`_open_event_store`）、
ディスク上に平文の DB は1つも無い。

**将来使うかもしれない移行コードは書かない**（設計原則7）。
必要になったときのために、**動く手順が spike で確認済みであること**だけを記録する。

## Alternatives

| 案 | 利点 | 採らなかった理由 |
|---|---|---|
| **SQLCipher（`sqlcipher3-binary`）** | 事実上の標準。DB-API 互換で `sqlite3` からの差分が小さい | **Windows wheel が無い。** 自前ビルドは OpenSSL を配布物に持ち込む |
| **列単位の暗号化（標準 `sqlite3` のまま）** | 依存を1つも増やさない | **FTS5 / sqlite-vec が使えない。** 検索できる平文が残るなら暗号化の意味が無い |
| **OS のファイル暗号化に委ねる（EFS / BitLocker）** | 実装ゼロ | ユーザー環境に依存し、**Lumi は「暗号化されている」と言えない。** 保証できないものを保証と書かない |
| **暗号化しない** | 最も簡単 | ADR-038 の否決事項。**黙って平文に落ちない** |

## Trade-offs

### 受け入れるコスト

- **配布物が +2.73 MiB**（R1 に対して 1% 未満。許容）
- **DB を開くのに 22 ms**（KDF）。起動時に1回。会話のクリティカルパスには乗らない
- **標準 `sqlite3` の知識が効かない箇所ができる。** 例外型が違う
  （`sqlite3.IntegrityError` → `apsw.ConstraintError`）、`fetchone()` が `Any | None` を返す
- **依存が1つ増える。** ネイティブ拡張であり、Python のマイナーバージョン更新に追随が要る

### 得るもの

- 会話ログ・記憶・ベクトル・FTS インデックスが**ディスク上で読めない**
- **ADR-038 を修正せずに済んだ。** 方針を実装が裏切らなかった
- `--self-check` が「**この配布物で暗号化が本当に効いているか**」を毎回答える

## Consequences

1. **`lumi/storage/sqlite.py` の `Database` が APSW 実装になった。** 呼び出し側の変更は例外型と `fetchone()` の扱いだけ
2. **`lumi/storage/secret.py` が増えた。** DB 鍵の生成と DPAPI への預け入れ。**鍵ファイルは privacy.md §2 の表に行として載る**
3. **`--self-check` に2項目増えた**（OS secret store / Encrypted SQLite）。配布物で暗号化が壊れていたら起動前に分かる
4. `lumi-core.spec` が **APSW の同梱をビルド時に強制する**（欠けたらビルドを失敗させる）
5. クレジット表示に **SQLite3 Multiple Ciphers** が載る（依存グラフに現れないため手で足した）
6. **イベント DB のオンディスク化は、まだしていない。** 保持期間ジョブと同じ変更で行う
   （Phase 2c）。消す手段が無いまま書き始めない
