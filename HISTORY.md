# MTG Deck Builder — 作業履歴

---

## 2026-03-24

### Streamlit Web UI の追加
- `app.py` を新規作成（Streamlit による Web フロントエンド）
- `requirements.txt` に `streamlit>=1.32.0` を追加
- 既存の `deck_builder` パッケージは変更せずに再利用

**UI 構成**
- サイドバー: デッキ一覧（radio）＋新規デッキ作成フォーム（expander）
- メインエリア: Cards タブ / Analyze タブ の2タブ構成

**Cards タブ**
- カード名 + 枚数 + ボタンで Scryfall から1枚追加
- カード一覧テーブル（枚数 / カード名 / タイプ / マナコスト / CMC / 削除ボタン）

**Analyze タブ**
- `st.metric()` x3: Total / Unique / Avg CMC
- マナカーブ棒グラフ
- 色分布棒グラフ
- カードタイプ分布棒グラフ

---

### カードタイプ列の追加
- `app.py` に `primary_type()` / `type_distribution()` を追加
- Cards タブのテーブルに「タイプ」列を追加
- Analyze タブに「カードタイプ分布」グラフを追加
- `type_line` から主要タイプ（Creature / Instant / Sorcery 等）を抽出

---

### 日本語カード名サポート
- `Card` dataclass に `printed_name: str = ""` フィールドを追加
- `display_name()` メソッドを追加（`printed_name/name` 形式で返す）
- `storage.py`: `printed_name` の保存・読み込みに対応
- `scryfall.py`:
  - 英語 fuzzy 検索が 404 の場合、`lang:ja` でフォールバック検索
  - 英語名検索時も `_fetch_japanese_name()` で日本語名を追加取得
  - キャッシュに `printed_name` がない場合は次回アクセス時に自動バックフィル
- 表示形式: `稲妻/Lightning Bolt`（日本語版が存在しないカードは英語名のみ）

---

### CSV インポート機能
- Cards タブ下部に `st.file_uploader` を追加（`.csv` のみ受け付け）
- フォーマット: ヘッダー行 `count,name`（BOM 付き UTF-8 対応）
- インポート実行時はプログレスバーで進捗表示
- 取得失敗カードは警告でまとめて表示、成功分のみデッキに追加
- サンプルファイル: `sample_import.csv`

---

### 起動スキルの登録
- `~/.claude/skills/start-deck-app/SKILL.md` にスキルを保存
- トリガー: 「起動」「アプリを起動」「streamlit起動」等

### 起動コマンド
```bash
# 初回のみ
pip install streamlit
mkdir -p ~/.streamlit
printf '[general]\nemail = ""\n' > ~/.streamlit/credentials.toml

# 毎回
cd /c/develop/deck
python -m streamlit run app.py --browser.gatherUsageStats false
```
→ ブラウザで http://localhost:8501 を開く

---

---

## 2026-03-28

### `printed_name` バックフィル修正（storage.py）
- **原因**: 既存デッキ JSON に `printed_name` が未保存だった
- **修正**: `storage.py` に `_cached_printed_name()` 追加
  - `load_deck` でデッキ JSON に `printed_name` がない場合、`.cache/<name>.json` から補完
  - API コールなし（ファイル読み取りのみ）
  - キャッシュにも `printed_name` がないカードは空のまま（次回 Scryfall 検索時に自動バックフィル）

### あいまい検索の候補ピッカー実装（scryfall.py / app.py）
- 参考: `C:\develop\mtg` の `scryfall_client.py` / `search_service.py` のフローを転用
- `scryfall.py` に2メソッド追加:
  - `get_card_exact(name)`: キャッシュ or `/cards/named?exact=` で完全一致。404なら `ValueError`
  - `search_candidates(query, max_results=8)`: `/cards/search?q=name:{query}` で候補一覧取得。英語ヒットなしの場合 `lang:ja` でフォールバック
- `app.py` のカード追加フローを変更:
  1. `get_card_exact()` → ヒット: そのまま追加
  2. `ValueError` → `search_candidates()` → 1件: 自動追加
  3. 複数件: セッションステート `_candidates` に格納、候補ピッカーUI表示
  4. 0件: エラー表示

---

## 2026-04-04

### テキスト貼り付け一括追加
- `枚数 カード名` / `カード名 枚数` 両形式に対応
- `Sideboard` / `サイドボード` 行を検出してそれ以降を自動的にサイド枠へ振り分け
- 複数候補ピッカーを bulk にも対応（`_paste_pending` session state）

### Adventure/DFC カード日本語名修正
- `_fetch_japanese_name` で `//` 前の名前を使って検索するよう修正
- CLB など一部 reprint は Scryfall データが英語のまま → ASCII-only 結果をスキップして非 ASCII 名を優先
- `search_candidates` の `_to_candidate` も `card_faces[0].printed_name` フォールバック追加
- `app.py` ロード後バックフィル：`printed_name` 空のカードを `get_card` で自動修正

### produced_mana / 土地分析
- `Card` に `produced_mana: list[str]` フィールド追加
- `storage.py` に保存・読み込み・キャッシュ補完（`_cached_produced_mana`）
- `analysis.py` に `land_stats` / `color_probability_by_turn`（超幾何分布）追加
- 確率グラフに `Any`（何色でも）・`All`（全色揃う、包除原理）ラインを追加

### メイン／サイドボード分離
- `Deck` に `sideboard` dict と `add_sideboard_card` / `remove_sideboard_card` / `move_to_sideboard` / `move_to_main` / `clear_sideboard` / `total_sideboard` / `list_sideboard` 追加
- `storage.py`：`sideboard` セクション保存・読み込み（後方互換）、`_card_to_dict` / `_dict_to_card` ヘルパー追加、`delete_deck` 追加
- `app.py`：追加先ラジオ（メイン/SB）、→SB / →Main 移動ボタン（↓/↑）、全削除ポップアップ、デッキ削除ポップアップ

### カードリスト2列表示
- 左列：スペル、右列：土地
- `_render_section` / `_render_card_rows` ヘルパーで共通化

### Analyze タブ改善
- `st.bar_chart` / `st.line_chart` → Altair に切り替え（スクロール干渉を解消）
- レイアウト：確率グラフ（全幅・最上段）→ マナカーブ｜タイプ分布 → 色分布｜マナ生成分布
- 土地メトリクスを2列外に出して色分布とマナ生成分布の高さを統一

## 未解決・次回確認事項
- なし
