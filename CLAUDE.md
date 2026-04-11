# CLAUDE.md

MTG Deck Builder の開発ガイド（Claude Code 向け）。

## Architecture

```
deck/
├── app.py                  # Streamlit Web UI（メインエントリ）
├── main.py                 # CLI エントリポイント
├── requirements.txt
├── sample_import.csv       # CSV インポートサンプル
├── deck_builder/           # コアパッケージ
│   ├── deck.py             # Card / Deck データクラス
│   ├── scryfall.py         # Scryfall API クライアント + キャッシュ
│   ├── storage.py          # デッキ JSON 保存・読み込み
│   ├── cli.py              # Typer CLI コマンド
│   └── analysis.py         # マナカーブ・色分布・統計
├── decks/                  # デッキ JSON（.gitignore 対象）
└── .cache/                 # Scryfall キャッシュ（.gitignore 対象）
```

## Common Commands

```bash
# Web UI 起動
python -m streamlit run app.py --browser.gatherUsageStats false

# CLI
python main.py --help
python main.py new "My Deck" --format modern
python main.py add "My Deck" "Lightning Bolt" 4
python main.py analyze "My Deck"
```

## Key Technical Constraints

### Windows エンコーディング（cp932）
- バーグラフ等に `█`/`░` を使わない → ASCII の `#`/`-` を使う
- Rich テーブルを stdout に出力する場合は UTF-8 でラップが必要:
  ```python
  Console(
      file=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"),
      highlight=False,
  )
  ```
  `force_terminal=True` だけでは不十分。

### Typer バージョン
- `>=0.12.0` 必須（0.9.0 は click 8.2.x と非互換でエラーになる）

### Scryfall API
- リクエスト間隔: `time.sleep(0.1)`（Scryfall ポリシー準拠）
- キャッシュキー: `name.lower().replace(" ", "_").replace("/", "-")`
- キャッシュ: `.cache/<normalized_name>.json`（1カード1ファイル）

## カード追加フロー（app.py）

```
ユーザー入力
  │
  ├─ get_card_exact(name)
  │   ├─ キャッシュヒット or Scryfall exact → 即追加
  │   └─ ValueError（404）↓
  │
  ├─ search_candidates(name)
  │   ├─ 0件 → エラー表示
  │   ├─ 1件 → get_card() で取得して追加
  │   └─ 複数件 → st.session_state._candidates に格納 → ピッカーUI表示
  │
  └─ ユーザーが候補を選択 → get_card(en_name) → 追加
```

## Session State（app.py）

| キー | 型 | 用途 |
|---|---|---|
| `selected_deck` | `str` | 選択中のデッキ名（ファイル stem） |
| `_deck_names` | `list[str]` | デッキ名リストキャッシュ |
| `_candidates` | `list[dict]` | 候補ピッカー用カードリスト |
| `_pending_count` | `int` | 候補選択時の追加枚数 |

## printed_name（日本語カード名）

- `Card.display_name()` → `稲妻/Lightning Bolt`（日本語名なしなら英語名のみ）
- `storage.py` の `load_deck` はデッキ JSON に `printed_name` がない場合、`.cache/` から補完
- `scryfall.py` の `get_card()` / `get_card_exact()` は取得時に自動バックフィル

## 開発ワークフロー（3軸）

新機能追加・改善を行う際は以下の3フェーズを順守する。

### Phase 1: プランナー
- ユーザーのリクエストを詳細仕様に展開する
- 変更対象ファイル・関数・セッションステートキーを特定する
- 実装前に評価基準を確認し、アプローチを確定する
- 不明点があれば `AskUserQuestion` で先に解消する
- `EnterPlanMode` を活用して計画をユーザーに提示・承認を得る

### Phase 2: ジェネレーター（実装）
- プランに沿って実装する
- 最小変更で目的を達成する（over-engineering 禁止）
- 既存のコードパターン・スタイルを踏襲する

### Phase 3: エバリュエーター（評価）
- 以下の評価基準に照らして自己評価を実施する
- Python 構文チェックは PostToolUse Hook が自動実行する
- 変更ファイルを読み返してコメントを残す

## 評価基準

| 基準 | 内容 |
|---|---|
| 機能性 | 既存機能が壊れていないか・新機能が仕様通り動くか |
| UX一貫性 | 既存の Streamlit UI スタイル（コンポーネント・レイアウト）と統一されているか |
| コード品質 | シンプル・可読性高い・重複なし・不要な抽象化なし |
| Session State | 適切なキー命名・不要な再レンダリングを起こしていないか |

## Reference

- 詳細仕様: `SPEC.md`
- 作業ログ: `HISTORY.md`
- 検索ロジック参考元: `C:\develop\mtg`（`services/scryfall_client.py`, `services/search_service.py`）
