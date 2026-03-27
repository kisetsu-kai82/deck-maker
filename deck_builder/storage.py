import json
from pathlib import Path

from .deck import Card, Deck

DECKS_DIR = Path(__file__).parent.parent / "decks"
CACHE_DIR = Path(__file__).parent.parent / ".cache"


def _deck_path(name: str) -> Path:
    safe = name.lower().replace(" ", "_")
    return DECKS_DIR / f"{safe}.json"


def _cached_printed_name(card_name: str) -> str:
    """Read printed_name from scryfall cache without making API calls."""
    safe = card_name.lower().replace(" ", "_").replace("/", "-")
    cache_file = CACHE_DIR / f"{safe}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data.get("printed_name", "")
        except Exception:
            pass
    return ""


def save_deck(deck: Deck) -> None:
    DECKS_DIR.mkdir(exist_ok=True)
    data = {
        "name": deck.name,
        "format": deck.format,
        "cards": [
            {
                "name": c.name,
                "mana_cost": c.mana_cost,
                "cmc": c.cmc,
                "colors": c.colors,
                "type_line": c.type_line,
                "count": c.count,
                "printed_name": c.printed_name,
            }
            for c in deck.list_cards()
        ],
    }
    _deck_path(deck.name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_deck(name: str) -> Deck:
    path = _deck_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Deck '{name}' not found.")
    data = json.loads(path.read_text(encoding="utf-8"))
    deck = Deck(data["name"], data.get("format", ""))
    for c in data["cards"]:
        card = Card(
            name=c["name"],
            mana_cost=c.get("mana_cost", ""),
            cmc=c.get("cmc", 0),
            colors=c.get("colors", []),
            type_line=c.get("type_line", ""),
            count=c["count"],
            printed_name=c.get("printed_name", "") or _cached_printed_name(c["name"]),
        )
        deck.cards[card.name.lower()] = card
    return deck


def list_decks() -> list[str]:
    DECKS_DIR.mkdir(exist_ok=True)
    return [p.stem for p in sorted(DECKS_DIR.glob("*.json"))]


def deck_exists(name: str) -> bool:
    return _deck_path(name).exists()
