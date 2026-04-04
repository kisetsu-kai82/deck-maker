# json モジュール: Python の辞書（dict）や リスト（list）を JSON 文字列に変換したり、
#   その逆（JSON 文字列 → Python オブジェクト）をしてくれる標準ライブラリ。
# pathlib.Path: ファイルパスをオブジェクトとして扱うクラス。
#   / 演算子でディレクトリを繋げたり、.read_text() / .write_text() でファイルを読み書きできる。
import json
from pathlib import Path

from .deck import Card, Deck  # 同じパッケージの deck.py から Card / Deck をインポート

# __file__ : このファイル自身のパス（例: C:\develop\deck\deck_builder\storage.py）
# .parent  : その親ディレクトリ（例: C:\develop\deck\deck_builder）
# .parent.parent : さらに上の親（例: C:\develop\deck） ← プロジェクトルート
DECKS_DIR = Path(__file__).parent.parent / "decks"   # デッキ JSON を保存するフォルダ
CACHE_DIR  = Path(__file__).parent.parent / ".cache"  # Scryfall キャッシュフォルダ


def _deck_path(name: str) -> Path:
    """デッキ名からファイルパスを生成する。

    例: "My Deck" → C:\develop\deck\decks\my_deck.json
    小文字にしてスペースを _ に換えることでファイル名を統一する。
    """
    safe = name.lower().replace(" ", "_")  # "My Deck" → "my_deck"
    return DECKS_DIR / f"{safe}.json"


# ── キャッシュ補完ヘルパー ────────────────────────────────────────────────────
# これらはデッキ JSON に情報が欠けているとき、
# Scryfall キャッシュファイルから不足分を補う関数。
# API を呼ばずファイルを読むだけなので高速。

def _cached_printed_name(card_name: str) -> str:
    """キャッシュ JSON から日本語カード名（printed_name）を取得する。
    キャッシュがない・読み取れない場合は空文字を返す。
    """
    # キャッシュのファイル名はカード名を正規化したもの（scryfall.py と同じルール）
    safe = card_name.lower().replace(" ", "_").replace("/", "-")
    cache_file = CACHE_DIR / f"{safe}.json"
    if cache_file.exists():
        try:
            # read_text() でファイル全体を文字列として読み込み、
            # json.loads() で Python の dict に変換する。
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            # dict.get(key, default): キーがなければ default（ここでは ""）を返す
            return data.get("printed_name", "")
        except Exception:
            # JSON が壊れているなど例外が出ても無視して空文字を返す
            pass
    return ""


def _cached_produced_mana(card_name: str) -> list:
    """キャッシュ JSON からマナ生成色リスト（produced_mana）を取得する。
    キャッシュがない・読み取れない場合は空リストを返す。
    """
    safe = card_name.lower().replace(" ", "_").replace("/", "-")
    cache_file = CACHE_DIR / f"{safe}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            # data.get("produced_mana") が None や存在しない場合は [] を返す
            # （None は falsy なので `or []` で空リストに統一）
            return data.get("produced_mana") or []
        except Exception:
            pass
    return []


# ── 保存用ヘルパー ────────────────────────────────────────────────────────────

def _card_to_dict(c: Card) -> dict:
    """Card オブジェクトを JSON に書き出せる辞書（dict）に変換する。
    dict にしてから json.dumps() に渡すと JSON 文字列になる。
    """
    return {
        "name":          c.name,
        "mana_cost":     c.mana_cost,
        "cmc":           c.cmc,
        "colors":        c.colors,
        "type_line":     c.type_line,
        "count":         c.count,
        "printed_name":  c.printed_name,
        "produced_mana": c.produced_mana,
    }


def save_deck(deck: Deck) -> None:
    """デッキを JSON ファイルに保存する。

    ファイルがなければ新規作成、あれば上書きする。
    """
    # mkdir(exist_ok=True): フォルダが既にあってもエラーにしない
    DECKS_DIR.mkdir(exist_ok=True)

    # デッキ全体を辞書にまとめる
    data = {
        "name":      deck.name,
        "format":    deck.format,
        # リスト内包表記: deck.list_cards() の各カードを _card_to_dict() で辞書に変換
        "cards":     [_card_to_dict(c) for c in deck.list_cards()],
        "sideboard": [_card_to_dict(c) for c in deck.list_sideboard()],
    }

    # json.dumps(): Python オブジェクト → JSON 文字列
    #   ensure_ascii=False: 日本語などの Unicode 文字をそのまま出力（エスケープしない）
    #   indent=2: 読みやすいよう2スペースでインデント
    _deck_path(deck.name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 読み込み用ヘルパー ────────────────────────────────────────────────────────

def _dict_to_card(c: dict) -> Card:
    """辞書（JSON から読み込んだもの）を Card オブジェクトに変換する。

    古い JSON には一部のキーがない場合があるため、.get(key, default) で
    デフォルト値を指定して KeyError を防いでいる。
    """
    return Card(
        name=c["name"],                          # 必須フィールドなので直接アクセス
        mana_cost=c.get("mana_cost", ""),        # 旧データになければ空文字
        cmc=c.get("cmc", 0),                     # 旧データになければ 0
        colors=c.get("colors", []),              # 旧データになければ空リスト
        type_line=c.get("type_line", ""),
        count=c["count"],
        # printed_name が JSON にあればそれを使い、なければキャッシュから補完する
        printed_name=c.get("printed_name", "") or _cached_printed_name(c["name"]),
        # produced_mana が JSON にあればそれを使い、なければキャッシュから補完する
        produced_mana=c.get("produced_mana") or _cached_produced_mana(c["name"]),
    )


def load_deck(name: str) -> Deck:
    """デッキ JSON ファイルを読み込んで Deck オブジェクトを返す。

    ファイルが存在しない場合は FileNotFoundError を送出する。
    """
    path = _deck_path(name)
    if not path.exists():
        # raise: 例外を発生させる。呼び出し元でエラーハンドリングできる。
        raise FileNotFoundError(f"Deck '{name}' not found.")

    # json.loads(): JSON 文字列 → Python の dict
    data = json.loads(path.read_text(encoding="utf-8"))

    deck = Deck(data["name"], data.get("format", ""))

    # メインデッキのカードを復元する
    for c in data["cards"]:
        card = _dict_to_card(c)
        # 辞書に直接セットすることで add_card の重複チェックをスキップし、
        # 保存時のデータをそのまま復元する
        deck.cards[card.name.lower()] = card

    # サイドボードを復元する（"sideboard" キーがない古いファイルでも空リストにフォールバック）
    for c in data.get("sideboard", []):
        card = _dict_to_card(c)
        deck.sideboard[card.name.lower()] = card

    return deck


# ── デッキ一覧・存在確認・削除 ────────────────────────────────────────────────

def list_decks() -> list[str]:
    """保存済みデッキ名の一覧をアルファベット順で返す。
    ファイル名の stem（拡張子を除いた部分）がデッキ名になる。
    """
    DECKS_DIR.mkdir(exist_ok=True)
    # Path.glob("*.json"): *.json にマッチするファイルを全て列挙する
    # p.stem: "my_deck.json" → "my_deck"
    return [p.stem for p in sorted(DECKS_DIR.glob("*.json"))]


def deck_exists(name: str) -> bool:
    """指定した名前のデッキが保存されているか確認する。"""
    return _deck_path(name).exists()


def delete_deck(name: str) -> None:
    """指定した名前のデッキ JSON ファイルを削除する。
    ファイルが存在しない場合は何もしない。
    """
    path = _deck_path(name)
    if path.exists():
        path.unlink()  # unlink(): ファイルを削除する Path のメソッド
