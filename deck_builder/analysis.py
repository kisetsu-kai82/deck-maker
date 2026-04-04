# combinations : 「n 個の中から r 個を選ぶ組み合わせ」を列挙するイテレータ。
#   包除原理の計算で「色のサブセット（部分集合）」を列挙するために使う。
# comb         : 二項係数 C(n, r) = n! / (r! * (n-r)!) を計算する関数。
#   超幾何分布の確率計算に使う。
from itertools import combinations
from math import comb

from .deck import Deck  # 同じパッケージ内の deck.py から Deck クラスをインポート

# 色コードから英語名への対応表（グラフの軸ラベルなどで使う）
COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def mana_curve(deck: Deck) -> dict[int, int]:
    """マナカーブを計算して返す。
    戻り値は {CMC: 枚数} の辞書（例: {1: 8, 2: 12, 3: 4}）。
    CMC（Converted Mana Cost）= そのカードを唱えるのに必要な総マナ数。
    """
    curve: dict[int, int] = {}
    for card in deck.list_cards():
        cmc = int(card.cmc)  # float → int に切り捨て（0.0 → 0）
        # dict.get(key, default) : キーがなければ default を返す便利メソッド
        curve[cmc] = curve.get(cmc, 0) + card.count
    # sorted() で CMC の小さい順に並べ直して辞書に戻す
    return dict(sorted(curve.items()))


def color_distribution(deck: Deck) -> dict[str, int]:
    """デッキ内の色分布を返す。
    戻り値は {色コード: 枚数} の辞書（例: {"R": 24, "U": 8}）。
    card.colors はそのカード自体の色（マナコストに含まれる色）。
    """
    dist: dict[str, int] = {}
    for card in deck.list_cards():
        for color in card.colors:
            dist[color] = dist.get(color, 0) + card.count
    return dist


def land_stats(deck: Deck) -> dict:
    """土地枚数・土地比率・マナ生成色分布を返す。
    戻り値は辞書で、次のキーを持つ:
      land_count    : 土地の総枚数
      total         : デッキの総枚数
      ratio         : 土地比率（%）
      produced_mana : {色コード: 枚数} （その色を出せる土地の合計枚数）
    """
    # type_line に "Land" が含まれているカードだけを抽出
    lands = [c for c in deck.list_cards() if "Land" in c.type_line]
    total = deck.total_cards()
    land_count = sum(c.count for c in lands)

    # 各土地が出せるマナの色を集計する
    produced: dict[str, int] = {}
    for card in lands:
        for color in card.produced_mana:
            produced[color] = produced.get(color, 0) + card.count

    return {
        "land_count": land_count,
        "total": total,
        # total が 0 のときにゼロ除算エラーが起きないよう三項演算子で守る
        "ratio": round(land_count / total * 100, 1) if total > 0 else 0.0,
        "produced_mana": produced,
    }


def color_probability_by_turn(deck: Deck, max_turn: int = 6) -> dict[str, list[float]]:
    """各ターンに各色の土地を1枚以上引いている確率を返す。

    ── 超幾何分布とは ──────────────────────────────────────────
    袋の中に N 個の玉（デッキ）があり、そのうち K 個が「当たり」（特定の色の土地）。
    そこから n 個を取り出したとき（手札 + ドロー）、当たりが 0 枚になる確率は:

        P(当たり 0 枚) = C(N-K, n) / C(N, n)

    C(a, b) は「a 個から b 個を選ぶ組み合わせ数」。
    求めたいのは「1枚以上引く確率」なので:

        P(1枚以上) = 1 - P(0枚)

    ターン N までに手札で見るカード枚数: 7（初手）+ N（ターン N のドローまで）
    ────────────────────────────────────────────────────────────

    戻り値: {色コード: [T1の確率, T2の確率, ..., T6の確率]} の辞書。
    "Any" キー = 何色でもいいのでマナを出せる土地を引いている確率。
    "All" キー = デッキに必要な全色が揃っている確率（包除原理で計算）。
    """
    d = deck.total_cards()  # デッキ総枚数 = N
    if d == 0:
        return {}

    # 各色について「その色を出せる土地の枚数 K」を集計する
    # "C" はカラーレス（無色）マナなので色確率の計算から除外する
    color_counts: dict[str, int] = {}
    for card in deck.list_cards():
        for color in card.produced_mana:
            if color != "C":
                color_counts[color] = color_counts.get(color, 0) + card.count

    result: dict[str, list[float]] = {}

    # 各色ごとに確率を計算する
    for color, k in color_counts.items():
        probs = []
        for turn in range(1, max_turn + 1):
            n = min(7 + turn, d)  # 手札枚数（デッキ枚数を超えないよう min）
            # P(0枚) = C(d-k, n) / C(d, n)
            # d-k < n のとき（外れ玉より多く引こうとしている）は確率 0
            p_zero = comb(d - k, n) / comb(d, n) if d - k >= n else 0.0
            probs.append(round((1 - p_zero) * 100, 1))
        result[color] = probs

    # Any: 「何色でもいいのでマナを出せる土地を1枚以上引く確率」
    # → マナを出せる土地を全部まとめて K とみなして同じ計算をする
    k_any = sum(
        c.count for c in deck.list_cards()
        if any(color != "C" for color in c.produced_mana)
    )
    if k_any:
        any_probs = []
        for turn in range(1, max_turn + 1):
            n = min(7 + turn, d)
            p_zero = comb(d - k_any, n) / comb(d, n) if d - k_any >= n else 0.0
            any_probs.append(round((1 - p_zero) * 100, 1))
        result["Any"] = any_probs

    # All: 「デッキの全必要色が手札に揃っている確率」を包除原理で計算する。
    #
    # ── 包除原理とは ───────────────────────────────────────────
    # A∩B（AもBも満たす）の確率を求めるには、
    # 「A を満たさない」「B を満たさない」の重複を足し引きして求める:
    #
    #   P(A∩B) = 1 - P(Ā) - P(B̄) + P(Ā∩B̄)
    #
    # 一般化すると色数 r の部分集合ごとに (-1)^r を掛けて合計する。
    # ────────────────────────────────────────────────────────────
    colors_list = [c for c in color_counts if c != "C"]
    if len(colors_list) >= 2:  # 2色以上のデッキでのみ意味がある
        all_probs = []
        cards = deck.list_cards()
        for turn in range(1, max_turn + 1):
            n = min(7 + turn, d)
            prob = 0.0
            # r = 0 は「空の部分集合」→ 寄与は +1（確率の全体）
            # r = 1, 2, ... は色の組み合わせを列挙して符号付きで加算
            for r in range(len(colors_list) + 1):
                sign = (-1) ** r  # r が偶数なら +1、奇数なら -1
                if r == 0:
                    prob += 1.0
                else:
                    # r 色の組み合わせを全列挙する
                    for subset in combinations(colors_list, r):
                        # subset に含まれる色を少なくとも1色出せる土地の枚数
                        k_T = sum(
                            c.count for c in cards
                            if any(col in c.produced_mana for col in subset)
                        )
                        # 「subset の色を1枚も引かない確率」
                        p_none = comb(d - k_T, n) / comb(d, n) if d - k_T >= n else 0.0
                        prob += sign * p_none
            # 浮動小数点誤差で微妙にマイナスになることがあるので max で 0 以上に丸める
            all_probs.append(round(max(prob, 0.0) * 100, 1))
        result["All"] = all_probs

    return result


def deck_stats(deck: Deck) -> dict:
    """デッキの基本統計を返す。
    戻り値:
      total   : 総枚数
      unique  : ユニーク種類数
      avg_cmc : 平均 CMC（加重平均）
    """
    total = deck.total_cards()
    cards = deck.list_cards()
    if total == 0:
        avg_cmc = 0.0
    else:
        # 加重平均: (CMC × 枚数) の合計 ÷ 総枚数
        avg_cmc = sum(c.cmc * c.count for c in cards) / total
    return {
        "total": total,
        "unique": len(cards),
        "avg_cmc": round(avg_cmc, 2),
    }
