# json     : Python 辞書 ↔ JSON 文字列の変換に使う標準ライブラリ
# time     : time.sleep() でリクエスト間隔を空けるために使う
# pathlib  : ファイルパスを操作するクラス Path を提供する標準ライブラリ
import json
import time
from pathlib import Path

# requests: HTTP リクエストを簡単に書けるサードパーティライブラリ。
#   requests.get(url, params=...) で GET リクエストを送り、
#   レスポンス（応答）オブジェクトを返してくれる。
import requests

# キャッシュを保存するフォルダのパス
CACHE_DIR = Path(__file__).parent.parent / ".cache"

# Scryfall API のベース URL
# Scryfall は MTG カード情報を無料で提供している公式 API サービス。
SCRYFALL_BASE = "https://api.scryfall.com"


class ScryfallClient:
    """Scryfall API を通じてカード情報を取得するクライアントクラス。

    ── キャッシュの仕組み ───────────────────────────────────────────
    同じカードを何度も API に問い合わせるのは無駄なので、
    一度取得したカード情報は .cache/<カード名>.json に保存しておく。
    次回からはそのファイルを読むだけで API 呼び出しを省ける。
    ────────────────────────────────────────────────────────────────
    """

    def __init__(self):
        # インスタンスを作ったとき、キャッシュフォルダがなければ作成する
        CACHE_DIR.mkdir(exist_ok=True)

    def _cache_path(self, name: str) -> Path:
        """カード名からキャッシュファイルのパスを計算する。

        例: "Lightning Bolt" → .cache/lightning_bolt.json
        スペースを _ に、/ を - に換えてファイル名を安全にする。
        """
        safe = name.lower().replace(" ", "_").replace("/", "-")
        return CACHE_DIR / f"{safe}.json"

    # ── カード取得（fuzzy 検索） ────────────────────────────────────────

    def get_card(self, name: str) -> dict:
        """カード名を fuzzy（あいまい）検索して情報を返す。

        fuzzy 検索: 完全一致でなくてもよく、Scryfall 側が一番近いカードを
        返してくれる。スペルミスや略称でもヒットしやすい。

        ── 処理フロー ───────────────────────────────────────────────
        1. キャッシュファイルがあれば読んで返す（API 呼び出しなし）
        2. なければ Scryfall の /cards/named?fuzzy= で検索する
        3. 404（見つからない）なら日本語名で再検索（_search_japanese）
        4. 取得したデータに日本語名がなければ _fetch_japanese_name で追加
        5. キャッシュに保存して返す
        ────────────────────────────────────────────────────────────
        """
        cache_file = self._cache_path(name)
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            # キャッシュが古くて printed_name がない場合は今回補完する
            if not data.get("printed_name"):
                data["printed_name"] = self._fetch_japanese_name(data["name"])
                cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data

        # Scryfall のポリシーで「100ms 以上の間隔を空ける」ことが求められている
        time.sleep(0.1)
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"fuzzy": name},  # fuzzy=... であいまい検索
            timeout=10,              # 10秒以内に応答がなければタイムアウト
        )

        if resp.status_code == 404:
            # 英語名では見つからなかった → 日本語名で再試行
            data = self._search_japanese(name)
        else:
            # 404 以外のエラー（500 など）なら例外を送出する
            resp.raise_for_status()
            # .json() : レスポンスの本文（JSON 文字列）を Python の dict に変換
            data = resp.json()

        # 日本語名がなければここで追加する
        if not data.get("printed_name"):
            data["printed_name"] = self._fetch_japanese_name(data["name"])

        # キャッシュに保存（次回から API 不要）
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ── カード取得（完全一致） ──────────────────────────────────────────

    def get_card_exact(self, name: str) -> dict:
        """カード名を完全一致で検索して返す。

        見つからない場合は ValueError を送出する（呼び出し元でキャッチする）。
        完全一致なので fuzzy より高速・確実だが、
        スペルミスがあると即 ValueError になる。
        """
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
            params={"exact": name},  # exact=... で完全一致検索
            timeout=10,
        )
        if resp.status_code == 404:
            # 完全一致で見つからなかったとき → ValueError で呼び出し元に伝える
            raise ValueError(f"Card '{name}' not found.")
        resp.raise_for_status()
        data = resp.json()
        if not data.get("printed_name"):
            data["printed_name"] = self._fetch_japanese_name(data["name"])
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ── 候補カード一覧の取得 ────────────────────────────────────────────

    @staticmethod
    def _sort_candidates_by_exact(candidates: list[dict], query: str) -> list[dict]:
        """完全一致（英語名または日本語名）を先頭に並べて返す。
        完全一致がない場合は前方一致 → クエリ長との差が小さい順にフォールバック。
        """
        q = query.lower()
        def _key(c):
            en = c["en_name"].lower()
            ja = c["ja_name"].lower()
            name = ja if ja else en
            if en == q or (ja and ja == q):
                return (0, 0)        # 完全一致
            if name.startswith(q) or en.startswith(q):
                return (1, len(name))  # 前方一致（短いほど優先）
            return (2, abs(len(name) - len(q)))  # 長さが近いほど優先
        return sorted(candidates, key=_key)

    def search_candidates(self, query: str, max_results: int = 8) -> list[dict]:
        """クエリに部分一致するカードの候補リストを返す。

        ユーザーが入力したテキストに複数のカードが該当するとき、
        この関数で候補一覧を取得してピッカー UI に渡す。

        ── 処理フロー ───────────────────────────────────────────────
        1. 英語名で name:{query} 検索
        2. ヒットしなければ lang:ja をつけて日本語名でフォールバック
        ────────────────────────────────────────────────────────────
        """

        def _to_candidate(c: dict) -> dict:
            """Scryfall の生データから UI 表示に必要な部分だけ抜き出す。

            Adventure カード（Bonecrusher Giant // Stomp など）は
            トップレベルに printed_name がなく card_faces[0] の中にある。
            """
            ja_name = c.get("printed_name") or ""
            # card_faces は両面・アドベンチャーカードで使われるリスト。
            # [0] が表面（フロント）。
            if not ja_name and c.get("card_faces"):
                ja_name = c["card_faces"][0].get("printed_name") or ""
            return {
                "en_name":   c.get("name", ""),
                "ja_name":   ja_name,
                "type_line": c.get("type_line", ""),
                "mana_cost": c.get("mana_cost", ""),
            }

        # 1. 英語名で検索
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                # unique=cards: 同名カードの再版（リプリント）を1枚にまとめる
                # order=name  : 名前順で並べる
                params={"q": f"name:{query}", "unique": "cards", "order": "name"},
                timeout=10,
            )
            if resp.status_code == 200:
                # .get("data", []): "data" キーがなければ空リストを返す
                # ソート前に切り捨てると完全一致が範囲外に落ちるため、全件取得してからソート→切り捨て
                cards = resp.json().get("data", [])
                if cards:
                    return self._sort_candidates_by_exact([_to_candidate(c) for c in cards], query)[:max_results]
        except Exception:
            pass  # ネットワークエラーなどは無視して次の検索へ

        # 2. 英語でヒットしなかったとき → 日本語名で再検索
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                params={"q": f"name:{query} lang:ja", "unique": "cards", "order": "name"},
                timeout=10,
            )
            if resp.status_code == 200:
                cards = resp.json().get("data", [])
                return self._sort_candidates_by_exact([_to_candidate(c) for c in cards], query)[:max_results]
        except Exception:
            pass

        return []  # どちらでも見つからなければ空リスト

    # ── 日本語名の取得 ──────────────────────────────────────────────────

    def _fetch_japanese_name(self, english_name: str) -> str:
        """英語名から日本語版の printed_name を取得する。

        ── 注意点 ────────────────────────────────────────────────────
        Adventure カードは英語名が "Bonecrusher Giant // Stomp" のように
        // で連結されているが、Scryfall の日本語検索では前半（フロント名）
        だけで検索しないとヒットしない。

        また、CLB（統率者レジェンズ）などの再版セットでは
        Scryfall データが英語のまま（printed_name が ASCII）になっていることがある。
        → 全結果を走査して、最初に見つかった「非 ASCII な名前」を採用する。
        ────────────────────────────────────────────────────────────
        戻り値: 日本語名（見つからなければ空文字）
        """
        # "Bonecrusher Giant // Stomp" → "Bonecrusher Giant"
        search_name = english_name.split(" // ")[0].strip()
        try:
            time.sleep(0.1)
            resp = requests.get(
                f"{SCRYFALL_BASE}/cards/search",
                # !"name" : 完全一致検索（感嘆符 + ダブルクォート）
                # lang:ja : 日本語版に絞る
                # unique=prints: 再版（リプリント）を全て列挙する（クセあり）
                params={"q": f'!"{search_name}" lang:ja', "unique": "prints"},
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json()
                for card in results.get("data", []):
                    # トップレベルの printed_name を試す
                    name = card.get("printed_name") or ""
                    # なければ card_faces[0] を試す
                    if not name and card.get("card_faces"):
                        name = card["card_faces"][0].get("printed_name") or ""
                    # str.isascii(): 文字列が ASCII 文字のみで構成されているか判定する。
                    # 日本語が含まれていれば isascii() == False なので、非 ASCII 名を採用する。
                    if name and not name.isascii():
                        return name
        except Exception:
            pass
        return ""

    # ── 日本語名での逆引き検索 ─────────────────────────────────────────

    def _search_japanese(self, name: str) -> dict:
        """日本語名（または日本語カード名）からカード情報を取得する。

        fuzzy 検索で英語名が見つからなかったとき、
        日本語名として Scryfall を検索し、
        そこから英語オラクルデータを取得して返す。

        ── 処理フロー ───────────────────────────────────────────────
        1. lang:ja 検索でヒットした日本語カードから英語名を取得
        2. /cards/named?exact= で英語オラクルデータを取得
        3. printed_name を日本語名で上書きして返す
        ────────────────────────────────────────────────────────────
        """
        time.sleep(0.1)
        resp = requests.get(
            f"{SCRYFALL_BASE}/cards/search",
            params={"q": f'!"{name}" lang:ja', "unique": "prints"},
            timeout=10,
        )
        # 4xx/5xx エラーは例外として送出
        resp.raise_for_status()
        results = resp.json()
        if not results.get("data"):
            raise ValueError(f"Card '{name}' not found in Japanese or English.")

        # 検索結果の最初のカードを使う
        ja_card = results["data"][0]
        english_name   = ja_card["name"]               # 英語名
        printed_name   = ja_card.get("printed_name", name)  # 日本語名（なければ入力名）

        # 英語オラクルデータを改めて取得する
        # （日本語版の JSON はマナコストなど一部フィールドが欠けることがある）
        time.sleep(0.1)
        oracle_resp = requests.get(
            f"{SCRYFALL_BASE}/cards/named",
            params={"exact": english_name},
            timeout=10,
        )
        oracle_resp.raise_for_status()
        data = oracle_resp.json()
        # 取得した英語データに日本語名を上書きする
        data["printed_name"] = printed_name
        return data
