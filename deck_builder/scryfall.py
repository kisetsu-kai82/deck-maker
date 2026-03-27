import json
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).parent.parent / ".cache"
SCRYFALL_BASE = "https://api.scryfall.com"


class ScryfallClient:
    def __init__(self):
        CACHE_DIR.mkdir(exist_ok=True)

    def _cache_path(self, name: str) -> Path:
        safe = name.lower().replace(" ", "_").replace("/", "-")
        return CACHE_DIR / f"{safe}.json"

    def get_card(self, name: str) -> dict:
        cache_file = self._cache_path(name)
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            # Backfill printed_name for caches created before this feature
            if not data.get("printed_name"):
                data["printed_name"] = self._fetch_japanese_name(data["name"])
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        # Scryfall requests a 50-100ms delay between requests
        time.sleep(0.1)
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"fuzzy": name},
            timeout=10,
        )

        if resp.status_code == 404:
            # Fuzzy search failed — try Japanese printed name search
            data = self._search_japanese(name)
        else:
            resp.raise_for_status()
            data = resp.json()

        # Always populate printed_name
        if not data.get("printed_name"):
            data["printed_name"] = self._fetch_japanese_name(data["name"])

        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def get_card_exact(self, name: str) -> dict:
        """Exact match only — raises ValueError if not found."""
        cache_file = self._cache_path(name)
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if not data.get("printed_name"):
                data["printed_name"] = self._fetch_japanese_name(data["name"])
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        time.sleep(0.1)
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"exact": name},
            timeout=10,
        )
        if resp.status_code == 404:
            raise ValueError(f"Card '{name}' not found.")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("printed_name"):
            data["printed_name"] = self._fetch_japanese_name(data["name"])
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def search_candidates(self, query: str, max_results: int = 8) -> list[dict]:
        """Return candidate cards matching query for user disambiguation."""
        def _to_candidate(c: dict) -> dict:
            return {
                "en_name": c.get("name", ""),
                "ja_name": c.get("printed_name", ""),
                "type_line": c.get("type_line", ""),
                "mana_cost": c.get("mana_cost", ""),
            }

        # English name search
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                params={"q": f"name:{query}", "unique": "cards", "order": "name"},
                timeout=10,
            )
            if resp.status_code == 200:
                cards = resp.json().get("data", [])[:max_results]
                if cards:
                    return [_to_candidate(c) for c in cards]
        except Exception:
            pass

        # Japanese name search fallback
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                params={"q": f"name:{query} lang:ja", "unique": "cards", "order": "name"},
                timeout=10,
            )
            if resp.status_code == 200:
                cards = resp.json().get("data", [])[:max_results]
                return [_to_candidate(c) for c in cards]
        except Exception:
            pass

        return []

    def _fetch_japanese_name(self, english_name: str) -> str:
        """Look up the Japanese printed name for a card by its English name."""
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                params={"q": f'!"{english_name}" lang:ja', "unique": "prints"},
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json()
                if results.get("data"):
                    return results["data"][0].get("printed_name", "")
        except Exception:
            pass
        return ""

    def _search_japanese(self, name: str) -> dict:
        """Search by Japanese printed name and return English oracle data."""
        time.sleep(0.1)
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/search",
            params={"q": f'!"{name}" lang:ja', "unique": "prints"},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results.get("data"):
            raise ValueError(f"Card '{name}' not found in Japanese or English.")

        ja_card = results["data"][0]
        english_name = ja_card["name"]
        printed_name = ja_card.get("printed_name", name)

        # Fetch the English oracle data so mana_cost / type_line etc. are populated
        time.sleep(0.1)
        oracle_resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"exact": english_name},
            timeout=10,
        )
        oracle_resp.raise_for_status()
        data = oracle_resp.json()
        data["printed_name"] = printed_name
        return data
