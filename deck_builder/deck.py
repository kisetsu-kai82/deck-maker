# dataclasses モジュールから @dataclass デコレータと field 関数をインポートする。
# @dataclass を使うと、クラスのフィールド（変数）を宣言するだけで
# __init__ などの定型コードを自動生成してくれる。
from dataclasses import dataclass, field


# ────────────────────────────────────────────
# Card クラス：1種類のカードの情報をまとめたもの
# ────────────────────────────────────────────
@dataclass  # このデコレータがあるだけで __init__ が自動生成される
class Card:
    name: str           # 英語の Oracle 名（例: "Lightning Bolt"）
    mana_cost: str      # マナコスト文字列（例: "{1}{R}"）
    cmc: float          # Converted Mana Cost＝総マナ数（例: 1.0）
    colors: list[str]   # カードの色識別子リスト（例: ["R"]）
    type_line: str      # タイプ行（例: "Instant"）
    count: int = 1      # デッキに入っている枚数（デフォルトは 1）
    printed_name: str = ""  # 日本語カード名（例: "稲妻"）。なければ空文字

    # list のデフォルト値は field(default_factory=list) で書く。
    # なぜなら Python では list をデフォルト引数に直接書くと
    # 全インスタンスで同じリストを共有してしまうバグが起きるため。
    produced_mana: list[str] = field(default_factory=list)
    # 例: Island なら ["U"]、Sacred Foundry なら ["R", "W"]

    def display_name(self) -> str:
        """画面表示用の名前を返す。
        日本語名があれば「稲妻/Lightning Bolt」形式、なければ英語名のみ。
        """
        if self.printed_name:
            return f"{self.printed_name}/{self.name}"
        return self.name


# ────────────────────────────────────────────
# Deck クラス：デッキ全体（メイン＋サイドボード）を管理する
# ────────────────────────────────────────────
class Deck:
    def __init__(self, name: str, format: str = ""):
        """デッキを新規作成する。
        name   : デッキ名（例: "Burn"）
        format : フォーマット名（例: "Modern"）。省略可。
        """
        self.name = name
        self.format = format

        # カードを辞書（dict）で管理する。
        # キーはカード英語名を小文字にしたもの（例: "lightning bolt"）。
        # 辞書にすることで「同じカードをもう1枚追加」のとき O(1) で検索できる。
        self.cards: dict[str, Card] = {}        # メインデッキ
        self.sideboard: dict[str, Card] = {}    # サイドボード

    # ── メインデッキ操作 ──────────────────────

    def add_card(self, card: Card, count: int = 1) -> None:
        """カードをメインデッキに追加する。
        すでに同名カードがあれば枚数を増やし、なければ新規登録する。
        """
        key = card.name.lower()  # 大文字小文字を無視するため小文字に統一
        if key in self.cards:
            # すでに登録済み → 枚数だけ増やす
            self.cards[key].count += count
        else:
            # 新規登録 → 枚数をセットしてから辞書に追加
            card.count = count
            self.cards[key] = card

    def remove_card(self, name: str) -> bool:
        """指定した名前のカードをメインデッキから削除する。
        削除できたら True、見つからなければ False を返す。
        """
        key = name.lower()
        if key in self.cards:
            del self.cards[key]
            return True
        return False

    # ── サイドボード操作 ──────────────────────

    def add_sideboard_card(self, card: Card, count: int = 1) -> None:
        """カードをサイドボードに追加する（add_card のサイドボード版）。"""
        key = card.name.lower()
        if key in self.sideboard:
            self.sideboard[key].count += count
        else:
            card.count = count
            self.sideboard[key] = card

    def remove_sideboard_card(self, name: str) -> bool:
        """指定した名前のカードをサイドボードから削除する。"""
        key = name.lower()
        if key in self.sideboard:
            del self.sideboard[key]
            return True
        return False

    # ── メイン ⇔ サイドボード 移動 ──────────

    def move_to_sideboard(self, name: str) -> None:
        """メインデッキのカードをサイドボードへ移動する。
        pop() で辞書からカードを取り出し、サイドに追加し直す。
        """
        key = name.lower()
        if key in self.cards:
            card = self.cards.pop(key)  # メインから取り出す
            self.add_sideboard_card(card, card.count)

    def move_to_main(self, name: str) -> None:
        """サイドボードのカードをメインデッキへ移動する。"""
        key = name.lower()
        if key in self.sideboard:
            card = self.sideboard.pop(key)  # サイドから取り出す
            self.add_card(card, card.count)

    # ── 一覧・集計 ────────────────────────────

    def list_cards(self) -> list[Card]:
        """メインデッキのカード一覧を返す（CMC → 名前 の順でソート済み）。
        sorted() の key には「何を基準に並べるか」のラムダ関数を渡す。
        タプル (cmc, name) を返すことで「CMC が同じなら名前順」になる。
        """
        return sorted(self.cards.values(), key=lambda c: (c.cmc, c.name))

    def list_sideboard(self) -> list[Card]:
        """サイドボードのカード一覧を返す（同じく CMC → 名前 順）。"""
        return sorted(self.sideboard.values(), key=lambda c: (c.cmc, c.name))

    def total_cards(self) -> int:
        """メインデッキの総枚数を返す。
        ジェネレータ式で各カードの count を合計している。
        """
        return sum(c.count for c in self.cards.values())

    def total_sideboard(self) -> int:
        """サイドボードの総枚数を返す。"""
        return sum(c.count for c in self.sideboard.values())

    # ── 一括削除 ──────────────────────────────

    def clear_cards(self) -> None:
        """メインデッキのカードをすべて削除する。"""
        self.cards.clear()  # 辞書の組み込みメソッドで全要素を消す

    def clear_sideboard(self) -> None:
        """サイドボードのカードをすべて削除する。"""
        self.sideboard.clear()
