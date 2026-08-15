---
paths:
  - ".claude/rules/**/*.md"
  - "CLAUDE.md"
---

# .claude/rules/

Lumi のコーディングルール。**`CLAUDE.md` は薄く保ち、守らせたいルールはここに置く。**

> このファイル自体は `paths` を持つので、**ルールや `CLAUDE.md` を編集するときにだけ**読み込まれる。
> 無条件ロードにすると毎セッション context を消費してしまうため。

## なぜ分けるのか

`CLAUDE.md` は System Prompt ではなく、**セッション開始時の User Message** として注入される。
会話が進むほど埋もれ、セッション後半では効きにくくなる。

`paths` frontmatter を持つルールは、**Claude が該当ファイルを読んだタイミングで注入される**。
より最近のメッセージとして働くため、実際に効く。

| 置き場所 | 内容 |
|---|---|
| `CLAUDE.md` | プロジェクト概要・技術スタック・リポジトリ構成・ドキュメントの地図・セッション開始時の手順 |
| `rules/0*.md`（無条件） | 常に効いていないと困るもの。**短く保つ**（毎セッション context を消費する） |
| `rules/*.md`（`paths` 付き） | 特定の領域を触るときだけ効けばよいもの |

## 一覧

### 無条件（毎セッション読み込まれる）

| ファイル | 内容 |
|---|---|
| `00-invariants.md` | 8つの不変条件。**例外なし** |
| `01-design-process.md` | 設計が先・コードが後。ADR の書き方。SSoT の規則 |
| `02-git.md` | ブランチ・コミット |

### path-scoped（該当ファイルを触ったときだけ）

| ファイル | `paths` |
|---|---|
| `python-core.md` | `core/**/*.py` |
| `kernel.md` | `core/lumi/kernel/**`, `core/lumi/agent/**` |
| `permission-tools.md` | `core/lumi/permission/**`, `core/lumi/tools/**` |
| `provenance.md` | `core/lumi/{agent,memory,permission,tools,providers}/**` |
| `audio.md` | `core/lumi/audio/**` |
| `memory.md` | `core/lumi/{memory,storage,world,internal}/**` |
| `events.md` | `core/lumi/kernel/event*`, `command*`, `hook*`, `core/lumi/transport/**` |
| `shell-rust.md` | `shell/**` |
| `stage-ts.md` | `stage/**` |
| `extensions.md` | `extensions/**`, `core/lumi/extensions/**`, `content/**` |
| `tests.md` | テストファイル |
| `docs.md` | `docs/**/*.md` |

## 書くときの方針

1. **設計を再説明しない。** ルールは「実装時に守ること」＋「定義は docs のどこにあるか」だけ書く。
   設計の中身を写すと [DESIGN.md §12](../../docs/DESIGN.md) の SSoT 規則を破り、必ずドリフトする
2. **1ファイル1トピック**
3. **具体的に書く。** 「適切にテストする」ではなく「LLM を呼ばずにテストできること」
4. **禁止事項には理由を添える。** 理由の無い禁止は、状況が変わったときに判断できない

## 注意

- ルールは**強制ではなく context**。確実に止めたい操作は `.claude/settings.json` の `permissions.deny` や
  `PreToolUse` フックで止める
- `/compact` の後、`paths` 付きルールは**自動では再注入されない**（次に該当ファイルを読んだときに再ロードされる）
- 読み込まれているルールの確認は `/context`、編集は `/memory`
