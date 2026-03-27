import io
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# Windows cp932 console cannot encode Unicode box-drawing / em-dash chars.
# Wrap stdout with UTF-8 and disable legacy Windows renderer.
_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
console = Console(file=_out, highlight=False)

from .deck import Deck
from .scryfall import ScryfallClient
from .storage import deck_exists, list_decks, load_deck, save_deck
from .analysis import color_distribution, deck_stats, mana_curve

app = typer.Typer(help="MTG Deck Builder CLI")
scryfall = ScryfallClient()

COLOR_SYMBOLS = {"W": "[bold white]W[/]", "U": "[bold blue]U[/]", "B": "[bold]B[/]", "R": "[bold red]R[/]", "G": "[bold green]G[/]"}


def _bar(count: int, max_count: int, width: int = 20) -> str:
    filled = int(count / max_count * width) if max_count else 0
    return "#" * filled + "-" * (width - filled)


@app.command()
def new(deck_name: str = typer.Argument(..., help="Name of the new deck"),
        format: str = typer.Option("", "--format", "-f", help="Format (e.g. standard, modern)")):
    """Create a new empty deck."""
    if deck_exists(deck_name):
        rprint(f"[yellow]Deck '{deck_name}' already exists.[/yellow]")
        raise typer.Exit(1)
    deck = Deck(deck_name, format)
    save_deck(deck)
    rprint(f"[green]Created deck:[/green] {deck_name}")


@app.command()
def add(
    deck_name: str = typer.Argument(..., help="Deck name"),
    card_name: str = typer.Argument(..., help="Card name to add"),
    count: int = typer.Option(1, "--count", "-n", help="Number of copies"),
):
    """Add a card to a deck (fetches data from Scryfall)."""
    if not deck_exists(deck_name):
        rprint(f"[red]Deck '{deck_name}' not found. Create it first with 'new'.[/red]")
        raise typer.Exit(1)

    with console.status(f"Fetching '{card_name}' from Scryfall..."):
        try:
            data = scryfall.get_card(card_name)
        except Exception as e:
            rprint(f"[red]Error fetching card: {e}[/red]")
            raise typer.Exit(1)

    from .deck import Card
    card = Card(
        name=data.get("name", card_name),
        mana_cost=data.get("mana_cost", ""),
        cmc=data.get("cmc", 0),
        colors=data.get("colors", []),
        type_line=data.get("type_line", ""),
        count=count,
    )

    deck = load_deck(deck_name)
    deck.add_card(card, count)
    save_deck(deck)
    rprint(f"[green]Added[/green] {count}x {card.name} to '{deck_name}'")


@app.command("remove")
def remove_card(
    deck_name: str = typer.Argument(..., help="Deck name"),
    card_name: str = typer.Argument(..., help="Card name to remove"),
):
    """Remove a card from a deck."""
    if not deck_exists(deck_name):
        rprint(f"[red]Deck '{deck_name}' not found.[/red]")
        raise typer.Exit(1)

    deck = load_deck(deck_name)
    if deck.remove_card(card_name):
        save_deck(deck)
        rprint(f"[green]Removed[/green] '{card_name}' from '{deck_name}'")
    else:
        rprint(f"[yellow]Card '{card_name}' not found in '{deck_name}'.[/yellow]")
        raise typer.Exit(1)


@app.command("list")
def list_deck(deck_name: str = typer.Argument(..., help="Deck name")):
    """List all cards in a deck."""
    if not deck_exists(deck_name):
        rprint(f"[red]Deck '{deck_name}' not found.[/red]")
        raise typer.Exit(1)

    deck = load_deck(deck_name)
    stats = deck_stats(deck)

    table = Table(title=f"[bold]{deck.name}[/bold]" + (f" [{deck.format}]" if deck.format else ""))
    table.add_column("Count", justify="right", style="cyan")
    table.add_column("Card Name", style="bold")
    table.add_column("Mana Cost")
    table.add_column("CMC", justify="right")
    table.add_column("Type")
    table.add_column("Colors")

    for card in deck.list_cards():
        colors = " ".join(COLOR_SYMBOLS.get(c, c) for c in card.colors) or "-"
        table.add_row(str(card.count), card.name, card.mana_cost or "-", str(int(card.cmc)), card.type_line, colors)

    console.print(table)
    console.print(f"Total: [bold]{stats['total']}[/bold] cards ({stats['unique']} unique) | Avg CMC: [bold]{stats['avg_cmc']}[/bold]")


@app.command()
def analyze(deck_name: str = typer.Argument(..., help="Deck name")):
    """Analyze a deck: mana curve, color distribution, stats."""
    if not deck_exists(deck_name):
        rprint(f"[red]Deck '{deck_name}' not found.[/red]")
        raise typer.Exit(1)

    deck = load_deck(deck_name)
    curve = mana_curve(deck)
    colors = color_distribution(deck)
    stats = deck_stats(deck)

    console.print(f"\n[bold underline]Analysis: {deck.name}[/bold underline]\n")

    # Mana curve
    console.print("[bold]Mana Curve:[/bold]")
    max_count = max(curve.values(), default=1)
    for cmc, count in curve.items():
        bar = _bar(count, max_count)
        console.print(f"  CMC {cmc}: [cyan]{bar}[/cyan] {count}")

    # Color distribution
    console.print("\n[bold]Color Distribution:[/bold]")
    total_colored = sum(colors.values()) or 1
    color_parts = []
    for sym, cnt in sorted(colors.items()):
        pct = round(cnt / total_colored * 100)
        color_parts.append(f"{COLOR_SYMBOLS.get(sym, sym)}({pct}%)")
    console.print("  " + " ".join(color_parts) if color_parts else "  Colorless")

    # Stats
    console.print(f"\n[bold]Stats:[/bold]")
    console.print(f"  Total: [bold]{stats['total']}[/bold] cards | Unique: {stats['unique']} | Avg CMC: [bold]{stats['avg_cmc']}[/bold]")
    if deck.format:
        console.print(f"  Format: {deck.format}")
    console.print()


@app.command()
def decks():
    """List all saved decks."""
    names = list_decks()
    if not names:
        rprint("[yellow]No decks found.[/yellow]")
        return

    table = Table(title="Saved Decks")
    table.add_column("Deck Name", style="bold")
    table.add_column("Cards", justify="right")
    table.add_column("Format")

    for name in names:
        try:
            deck = load_deck(name.replace("_", " "))
        except Exception:
            # Try with underscore-based name
            try:
                from .storage import DECKS_DIR
                import json
                data = json.loads((DECKS_DIR / f"{name}.json").read_text(encoding="utf-8"))
                from .deck import Card
                deck = Deck(data["name"], data.get("format", ""))
                for c in data["cards"]:
                    card = Card(**{k: c[k] for k in ("name", "mana_cost", "cmc", "colors", "type_line", "count")})
                    deck.cards[card.name.lower()] = card
            except Exception:
                table.add_row(name, "?", "?")
                continue
        stats = deck_stats(deck)
        table.add_row(deck.name, str(stats["total"]), deck.format or "-")

    console.print(table)
