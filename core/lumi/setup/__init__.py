"""初回セットアップ — 外部エンジンの取得・検証・検出。

設計 → docs/architecture/setup.md / 決定 → docs/decisions/ADR-019-tts-engine-distribution.md

**ユーザーが選ぶまで外部通信しない。** このパッケージの中で HTTP を触るのは
`install.py` の `install_engine` だけであり、それはユーザーの選択を受けてから呼ばれる。
"""
