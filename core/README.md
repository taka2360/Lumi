# Lumi Core

Python / asyncio。**権威**（判断・状態・ポリシー・記憶）を持つ単一プロセス。Shell も Stage も Extension も Core のクライアント。

設計 → [../docs/architecture/core.md](../docs/architecture/core.md)

## セットアップ

```sh
uv sync            # 依存の解決と .venv の作成（Python 3.12）
```

## 実行

```sh
uv run lumi-core                 # WS サーバを起動（既定 127.0.0.1、ポートは自動割当）
```

Shell からサイドカーとして起動される場合、接続トークンは環境変数 `LUMI_WS_TOKEN` で渡される
（コマンドラインに載せない → [../docs/interfaces/shell.md](../docs/interfaces/shell.md)）。

## 検査

```sh
uv run pytest      # テスト
uv run ruff check  # lint
uv run ruff format # format
uv run mypy        # 型チェック（strict）
```
