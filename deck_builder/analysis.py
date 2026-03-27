from .deck import Deck

COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}


def mana_curve(deck: Deck) -> dict[int, int]:
    curve: dict[int, int] = {}
    for card in deck.list_cards():
        cmc = int(card.cmc)
        curve[cmc] = curve.get(cmc, 0) + card.count
    return dict(sorted(curve.items()))


def color_distribution(deck: Deck) -> dict[str, int]:
    dist: dict[str, int] = {}
    for card in deck.list_cards():
        for color in card.colors:
            dist[color] = dist.get(color, 0) + card.count
    return dist


def deck_stats(deck: Deck) -> dict:
    total = deck.total_cards()
    cards = deck.list_cards()
    if total == 0:
        avg_cmc = 0.0
    else:
        avg_cmc = sum(c.cmc * c.count for c in cards) / total
    return {
        "total": total,
        "unique": len(cards),
        "avg_cmc": round(avg_cmc, 2),
    }
