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

## Reference

- 詳細仕様: `SPEC.md`
- 作業ログ: `HISTORY.md`
- 検索ロジック参考元: `C:\develop\mtg`（`services/scryfall_client.py`, `services/search_service.py`）
