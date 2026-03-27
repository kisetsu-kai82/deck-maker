# MTG Deck Builder — 仕様書

---

## プロジェクト構成

```
deck/
├── app.py                  # Streamlit Web UI
├── main.py                 # CLI エントリポイント
├── requirements.txt
├── sample_import.csv       # CSVインポート用サンプル
├── HISTORY.md
├── SPEC.md
├── deck_builder/
│   ├── __init__.py
│   ├── cli.py              # Typer CLI コマンド
│   ├── deck.py             # Card / Deck クラス
│   ├── scryfall.py         # Scryfall API クライアント
│   ├── storage.py          # JSON 保存・読み込み
│   └── analysis.py         # 分析ロジック
├── decks/                  # デッキ JSON ファイル保存先
└── .cache/                 # Scryfall API レスポンスキャッシュ
```

---

## データモデル

### Card（`deck_builder/deck.py`）

| フィールド | 型 | 説明 |
|---|---|---|
| `name` | `str` | 英語カード名（Scryfall canonical） |
| `mana_cost` | `str` | マナコスト文字列（例: `{R}`） |
| `cmc` | `float` | 換算マナコスト |
| `colors` | `list[str]` | 色記号リスト（例: `["R", "U"]`） |
| `type_line` | `str` | タイプライン（例: `Creature — Human Wizard`） |
| `count` | `int` | 枚数（デフォルト: 1） |
| `printed_name` | `str` | 日本語カード名（例: `稲妻`）。存在しない場合は空文字 |

**`display_name()`**: `printed_name` がある場合 `稲妻/Lightning Bolt`、ない場合 `Lightning Bolt` を返す。

### Deck（`deck_builder/deck.py`）

| フィールド | 型 | 説明 |
|---|---|---|
| `name` | `str` | デッキ名 |
| `format` | `str` | フォーマット（例: `Modern`） |
| `cards` | `dict[str, Card]` | カード名（lowercase）→ Card |

---

## ストレージ（`deck_builder/storage.py`）

- 保存先: `decks/<deck_name_lowercase_underscored>.json`
- エンコーディング: UTF-8
- 形式:

```json
{
  "name": "Modern Burn",
  "format": "Modern",
  "cards": [
    {
      "name": "Lightning Bolt",
      "mana_cost": "{R}",
      "cmc": 1.0,
      "colors": ["R"],
      "type_line": "Instant",
      "count": 4,
      "printed_name": "稲妻"
    }
  ]
}
```

---

## Scryfall API（`deck_builder/scryfall.py`）

- ベース URL: `https://api.scryfall.com`
- キャッシュ: `.cache/<card_name_normalized>.json`（1カード1ファイル）
- リクエスト間隔: 100ms（Scryfall ポリシー準拠）

### カード取得フロー

```
get_card(name)
  │
  ├─ キャッシュあり → printed_name がなければバックフィル → 返す
  │
  ├─ /cards/named?fuzzy=<name> → 200 → printed_name がなければ
  │                                    _fetch_japanese_name() で取得
  │
  └─ 404 → _search_japanese(name)
              │
              ├─ /cards/search?q="<name>" lang:ja → 日本語カード取得
              └─ /cards/named?exact=<english_name> → oracle データ取得
                                                      + printed_name を注入
```

**`_fetch_japanese_name(english_name)`**: `/cards/search?q=!"<name>" lang:ja` で日本語印刷名を取得。日本語版が存在しない場合は空文字を返す。

---

## 分析（`deck_builder/analysis.py`）

| 関数 | 戻り値 | 説明 |
|---|---|---|
| `mana_curve(deck)` | `dict[int, int]` | CMC → 枚数 |
| `color_distribution(deck)` | `dict[str, int]` | 色記号 → 枚数 |
| `deck_stats(deck)` | `dict` | `total` / `unique` / `avg_cmc` |

---

## Web UI（`app.py`）

### セッションステート
- `st.session_state.selected_deck`: 選択中のデッキ名（ファイル stem）
- `st.session_state._deck_names`: デッキ名リストのキャッシュ

### サイドバー
- デッキ一覧: `list_decks()` → `st.radio()`
- 新規作成: `st.expander` 内フォーム → `Deck()` → `save_deck()`

### Cards タブ
- 1枚追加: テキスト入力 + 枚数 + ボタン（日本語名・英語名両対応）
- カード一覧: 枚数 / `display_name()` / タイプ / マナコスト / CMC / 削除ボタン
- CSVインポート: `st.file_uploader`（`.csv`）→ `count,name` 列を解析

### Analyze タブ
- `st.metric()` x3: Total / Unique / Avg CMC
- マナカーブ棒グラフ（CMC → 枚数）
- 色分布棒グラフ（White / Blue / Black / Red / Green）
- カードタイプ分布棒グラフ（Creature / Instant / Sorcery 等）

---

## CSV インポートフォーマット

```csv
count,name
4,Lightning Bolt
4,稲妻
2,Counterspell
```

- ヘッダー行必須（`count`, `name` 列）
- エンコーディング: UTF-8 / UTF-8 BOM どちらも可
- `name` は英語名・日本語名どちらでも可（Scryfall で解決）

---

## 起動方法

```bash
# Web UI
python -m streamlit run app.py --browser.gatherUsageStats false

# CLI
python main.py --help
```
