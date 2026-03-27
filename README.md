# MTG Deck Builder

Magic: The Gathering のデッキを管理する Web アプリ + CLI ツール。
Scryfall と連携してカード情報を自動取得します。

## Features

- **Web UI** — Streamlit によるデッキ管理画面
- **日本語対応** — カード名を `稲妻/Lightning Bolt` 形式で表示
- **あいまい検索** — 部分名で検索すると候補リストから選択して追加
- **CSV インポート** — `count,name` 形式の CSV でまとめてカードを追加
- **デッキ分析** — マナカーブ / 色分布 / カードタイプ分布グラフ
- **CLI** — コマンドラインからもデッキ操作可能

## Setup

```bash
pip install -r requirements.txt

# Streamlit の初回設定（メールプロンプトをスキップ）
mkdir -p ~/.streamlit
printf '[general]\nemail = ""\n' > ~/.streamlit/credentials.toml
```

## Usage

### Web UI

```bash
python -m streamlit run app.py --browser.gatherUsageStats false
```

ブラウザで http://localhost:8501 を開く。

### CLI

```bash
python main.py --help
python main.py new "Modern Burn" --format modern
python main.py add "Modern Burn" "Lightning Bolt" 4
python main.py list "Modern Burn"
python main.py analyze "Modern Burn"
python main.py decks
```

### CSV インポート

Web UI の Cards タブ下部からアップロード。フォーマット:

```csv
count,name
4,Lightning Bolt
4,稲妻
2,Counterspell
```

- ヘッダー行（`count`, `name`）必須
- エンコーディング: UTF-8 / UTF-8 BOM どちらも可
- カード名は英語・日本語どちらでも対応（Scryfall で解決）

## Requirements

- Python 3.10+
- 依存パッケージは `requirements.txt` 参照
