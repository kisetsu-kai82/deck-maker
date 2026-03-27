from dataclasses import dataclass, field


@dataclass
class Card:
    name: str
    mana_cost: str
    cmc: float
    colors: list[str]
    type_line: str
    count: int = 1
    printed_name: str = ""

    def display_name(self) -> str:
        if self.printed_name:
            return f"{self.printed_name}/{self.name}"
        return self.name


class Deck:
    def __init__(self, name: str, format: str = ""):
        self.name = name
        self.format = format
        self.cards: dict[str, Card] = {}

    def add_card(self, card: Card, count: int = 1) -> None:
        key = card.name.lower()
        if key in self.cards:
            self.cards[key].count += count
        else:
            card.count = count
            self.cards[key] = card

    def remove_card(self, name: str) -> bool:
        key = name.lower()
        if key in self.cards:
            del self.cards[key]
            return True
        return False

    def list_cards(self) -> list[Card]:
        return sorted(self.cards.values(), key=lambda c: (c.cmc, c.name))

    def total_cards(self) -> int:
        return sum(c.count for c in self.cards.values())
