# csv     : CSV ファイルを読み書きするための標準ライブラリ
# io      : メモリ上で文字列をファイルのように扱う StringIO などを提供する
# re      : 正規表現（Regular Expression）ライブラリ。
#           パターンでテキストを検索・抽出するときに使う。
import csv
import io
import re

# streamlit: Python でインタラクティブな Web アプリを作れるライブラリ。
#   st.text_input() → テキスト入力欄
#   st.button()     → ボタン
#   st.columns()    → 横並びレイアウト
#   st.session_state → ページが再描画されても値を保持するストレージ
import streamlit as st

# 同パッケージ（deck_builder/）から分析関数をインポート
from deck_builder.analysis import (
    color_distribution,
    color_probability_by_turn,
    deck_stats,
    land_stats,
    mana_curve,
)


# ── ユーティリティ関数 ─────────────────────────────────────────────────────────

def primary_type(type_line: str) -> str:
    """type_line（例: "Creature — Elf Druid"）から主要タイプを抽出する。

    MTG のタイプ行は "Creature — ..." や "Instant" のような形をしている。
    「—」以前の部分を見て、既知のタイプが含まれていれば返す。
    """
    # split("—")[0] : "—" で区切った最初の部分（例: "Creature "）
    # .strip()       : 前後の空白を除去
    main = type_line.split("—")[0].strip()
    for t in ("Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Land", "Planeswalker", "Battle"):
        if t in main:
            return t
    # 既知タイプに一致しなければ main をそのまま返す（空なら "Unknown"）
    return main if main else "Unknown"


def type_distribution(deck) -> dict[str, int]:
    """デッキ内のカードタイプ別枚数を返す。
    戻り値は枚数の多い順にソートされた辞書（例: {"Creature": 20, "Land": 24}）。
    """
    dist: dict[str, int] = {}
    for card in deck.list_cards():
        t = primary_type(card.type_line)
        dist[t] = dist.get(t, 0) + card.count
    # sorted(): key に「枚数を逆順（マイナス）にしたもの」を指定して多い順に並べる
    return dict(sorted(dist.items(), key=lambda x: -x[1]))


_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _load_oracle_text(name: str) -> str:
    """カード名からキャッシュの oracle_text を取得する。DFC は各 face を結合。"""
    import json, pathlib
    key = name.lower().replace(" ", "_").replace("/", "-")
    cache_path = pathlib.Path(".cache") / f"{key}.json"
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if "card_faces" in data:
            return " ".join(f.get("oracle_text", "") for f in data["card_faces"])
        return data.get("oracle_text", "")
    except Exception:
        return ""


def _build_flat_deck(deck) -> list[dict]:
    """カードのcountを展開してシャッフル可能なflatリストを作る。
    例: 4x Lightning Bolt → [{name:..., ...}, ×4]
    各カードを独立した dict オブジェクトとして生成することで is 比較が正常動作する。
    """
    import random
    flat = []
    for card in deck.list_cards():
        entry = {
            "name": card.name,
            "display": card.display_name(),
            "mana_cost": card.mana_cost,
            "cmc": int(card.cmc),
            "type": primary_type(card.type_line),
            "type_line": card.type_line,
            "produced_mana": card.produced_mana or [],
            "oracle_text": _load_oracle_text(card.name),
        }
        flat.extend([dict(entry) for _ in range(card.count)])
    random.shuffle(flat)
    return flat


def _mana_emoji(mana_cost: str) -> str:
    """マナコスト文字列（例: {1}{U}{R}）を絵文字表現に変換する。"""
    if not mana_cost:
        return "-"
    mapping = {"W": "⬜", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢", "C": "⬛"}
    result = mana_cost
    for code, emoji in mapping.items():
        result = result.replace(f"{{{code}}}", emoji)
    result = re.sub(r'\{(\d+)\}', r'\1', result)
    return result


def _parse_mana_cost(cost_str: str) -> dict:
    """{2}{U}{R} → {"generic": 2, "U": 1, "R": 1}。X・ハイブリッドは無視。"""
    if not cost_str:
        return {}
    result: dict = {}
    for token in re.findall(r'\{([^}]+)\}', cost_str):
        if token.isdigit():
            result["generic"] = result.get("generic", 0) + int(token)
        elif token in ("W", "U", "B", "R", "G", "C"):
            result[token] = result.get(token, 0) + 1
    return result


def _compute_tapping(battlefield: list, tapped: list, cost_str: str, mana_pool: dict | None = None):
    """スペルを唱えるためにタップする土地インデックスのリストを返す。
    払えない場合は (None, None) を返す。成功時は (tap_indices, pool_after) を返す。

    フローティングマナ (mana_pool) を優先消費し、不足分を土地・マナクリの自動タップで補う。
    各土地は produced_mana の中から1色を選んで出力できる（1色=1マナ）。
    最も選択肢の少ない色要求を優先して割り当てる（最制約優先の貪欲法）。
    """
    cost = _parse_mana_cost(cost_str)
    if not cost:
        return [], dict(mana_pool) if mana_pool else {}

    pool = dict(mana_pool) if mana_pool else {}
    colored = {c: v for c, v in cost.items() if c != "generic"}
    generic = cost.get("generic", 0)

    # フローティングマナから色マナを優先消費
    remaining_colored = {}
    for color, cnt in colored.items():
        pool_have = pool.get(color, 0)
        consumed = min(pool_have, cnt)
        if consumed > 0:
            pool[color] = pool_have - consumed
            if pool[color] == 0:
                del pool[color]
        remaining = cnt - consumed
        if remaining > 0:
            remaining_colored[color] = remaining

    # フローティングマナからジェネリックを消費（残り全色を使用可）
    remaining_generic = generic
    if remaining_generic > 0:
        pool_total = sum(pool.values())
        consumed_g = min(pool_total, remaining_generic)
        if consumed_g > 0:
            remaining_generic -= consumed_g
            # プールから任意の色を消費（辞書順）
            for c in list(pool.keys()):
                if consumed_g <= 0:
                    break
                take = min(pool[c], consumed_g)
                pool[c] -= take
                consumed_g -= take
                if pool[c] == 0:
                    del pool[c]

    # 残りを土地・マナクリの自動タップで補う
    avail_idx = [i for i, t in enumerate(tapped) if not t]

    reqs = []
    for color, cnt in remaining_colored.items():
        reqs.extend([color] * cnt)
    reqs.sort(key=lambda c: sum(
        1 for i in avail_idx if c in battlefield[i].get("produced_mana", [])
    ))

    result_tapped = []
    remaining_avail = list(avail_idx)

    for color in reqs:
        matched = None
        for i in remaining_avail:
            if color in battlefield[i].get("produced_mana", []):
                matched = i
                break
        if matched is None:
            return None, None  # この色要求を満たせる土地がない
        result_tapped.append(matched)
        remaining_avail.remove(matched)

    # generic 分の土地が残っているか確認してタップ
    if len(remaining_avail) < remaining_generic:
        return None, None
    result_tapped.extend(remaining_avail[:remaining_generic])
    return result_tapped, pool


def _is_tapland(oracle_text: str) -> bool:
    """タップインランドかどうかを oracle_text から判定する。"""
    return "enters tapped" in oracle_text.lower()


def _is_fetchland(oracle_text: str) -> bool:
    """フェッチランドかどうかを oracle_text から判定する。"""
    t = oracle_text.lower()
    return "sacrifice" in t and "search your library for" in t and "put it onto the battlefield" in t


def _is_summoning_sick(perm: dict, turn: int) -> bool:
    """パーマネントが召喚酔い状態かどうかを返す。
    type_line に Creature が含まれ、Haste がなく、このターンに戦場へ出た場合に True。
    """
    if "Creature" not in perm.get("type_line", ""):
        return False
    if "haste" in perm.get("oracle_text", "").lower():
        return False
    return perm.get("entered_turn", -1) == turn


def _fetch_filter(oracle_text: str) -> str:
    """フェッチランドの oracle_text からサーチ対象の土地タイプ文字列を抽出する。
    例: 'Search your library for an Island or Mountain card' → 'island or mountain'
    """
    m = re.search(r'search your library for (?:a |an )?([\w\s]+?) card', oracle_text.lower())
    return m.group(1).strip() if m else ""


def _classify_land(oracle_text: str) -> dict:
    """土地の種別と付随データを分類する。
    戻り値: {"type": str, ...extra}
      type: fetch / shock / bounce / scry_land / fast / bfz / check / reveal / tapland / normal
    """
    if not oracle_text:
        return {"type": "normal"}
    t = oracle_text.lower()

    # フェッチランド (sacrifice → search library → put on battlefield)
    if "sacrifice" in t and "search your library for" in t and "put it onto the battlefield" in t:
        return {"type": "fetch"}

    # ショックランド ("you may pay 2 life. if you don't, it enters tapped")
    if "you may pay 2 life" in t and "if you don't, it enters tapped" in t:
        return {"type": "shock"}

    # バウンスランド (enters tapped + return a land you control to hand)
    if "enters tapped" in t and re.search(r"return a land you control to (?:its owner's|your) hand", t):
        return {"type": "bounce"}

    # スクライランド (enters tapped + "when NAME enters, scry N")
    m_scry = re.search(r'when .+? enters,? scry (\d+)', t)
    if m_scry and "enters tapped" in t:
        return {"type": "scry_land", "scry": int(m_scry.group(1))}

    # 諜報ランド (enters tapped + "when NAME enters, surveil N")
    m_surveil = re.search(r'when .+? enters,? surveil (\d+)', t)
    if m_surveil and "enters tapped" in t:
        return {"type": "surveil_land", "surveil": int(m_surveil.group(1))}

    # ファストランド ("enters tapped unless you control two or fewer other lands")
    if re.search(r'enters tapped unless you control two or fewer other lands', t):
        return {"type": "fast"}

    # BFZランド ("enters tapped unless you control two or more basic lands")
    if re.search(r'enters tapped unless you control two or more basic lands', t):
        return {"type": "bfz"}

    # チェックランド ("enters tapped unless you control a TYPE or an TYPE")
    m_check = re.search(r'enters tapped unless you control (?:a |an )([\w\s]+?)\.', t)
    if m_check:
        return {"type": "check", "requires": m_check.group(1).strip()}

    # リビールランド ("enters tapped unless you reveal a TYPE card from your hand")
    m_reveal = re.search(r'enters tapped unless you reveal (?:a |an )?([\w\s]+?) card from your hand', t)
    if m_reveal:
        return {"type": "reveal", "requires": m_reveal.group(1).strip()}

    # 通常タップイン
    if "enters tapped" in t:
        return {"type": "tapland"}

    return {"type": "normal"}


def _detect_hand_effects(oracle_text: str) -> list:
    """oracle_text から draw/discard/return/rummage/token 効果を検出する。
    may discard は任意なのでスキップ。draw は即時、discard/return/rummage はキュー。
    """
    if not oracle_text:
        return []
    effects = []
    text = oracle_text.lower()
    _num_pat = r'(a|an|\d+|two|three|four|five|six|seven)'

    def _n(s: str) -> int:
        return _NUM_WORDS.get(s, int(s) if s.isdigit() else 0)

    # ── Rummage (discard X → draw Y): 複合効果を先に検出してdouble-countを防ぐ ──
    _consumed: set[int] = set()
    for m in re.finditer(rf'discard {_num_pat} cards?,? then draw {_num_pat} cards?', text):
        n_disc, n_draw = _n(m.group(1)), _n(m.group(2))
        if n_disc > 0 and n_draw > 0:
            effects.append({"type": "rummage", "discard": n_disc, "draw": n_draw})
            _consumed.update(range(m.start(), m.end()))
    # 追加コスト型: "as an additional cost to cast this spell, discard X. Draw Y."
    for m_ac in re.finditer(rf'as an additional cost to cast this spell, discard {_num_pat} cards?', text):
        n_disc = _n(m_ac.group(1))
        m_dr = re.search(rf'draw {_num_pat} cards?', text[m_ac.end():])
        if m_dr and n_disc > 0:
            effects.append({"type": "rummage", "discard": n_disc, "draw": _n(m_dr.group(1))})
            _consumed.update(range(m_ac.start(), m_ac.end()))
            _consumed.update(range(m_ac.end() + m_dr.start(), m_ac.end() + m_dr.end()))

    # ── トークン生成 ────────────────────────────────────────────────
    for m in re.finditer(rf'create (?:up to )?{_num_pat} ([\w\s/+\-]+?) tokens?', text):
        n = _n(m.group(1))
        desc = m.group(2).strip().rstrip(".,")
        if n > 0 and desc:
            effects.append({"type": "token", "count": n, "desc": desc})

    # ── Draw (rummage消費済み位置はスキップ) ────────────────────────
    for m in re.finditer(rf'draw {_num_pat} cards?', text):
        if m.start() in _consumed:
            continue
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "draw", "count": n})

    # ── Discard (任意・消費済みはスキップ) ─────────────────────────
    for m in re.finditer(rf'discard {_num_pat} cards?', text):
        if m.start() in _consumed:
            continue
        start = max(0, m.start() - 4)
        if "may" not in text[start:m.start()]:
            n = _n(m.group(1))
            if n > 0:
                effects.append({"type": "discard", "count": n})

    for m in re.finditer(rf'put {_num_pat} cards? from your hand on top of your library', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "return", "count": n})

    for m in re.finditer(rf'scry {_num_pat}', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "scry", "count": n})

    for m in re.finditer(rf'surveil {_num_pat}', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "surveil", "count": n})

    for m in re.finditer(rf'mill {_num_pat}', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "mill", "count": n})

    for m in re.finditer(rf'look at the top {_num_pat} cards? of your library', text):
        after = text[m.end():]
        if re.search(r'put (?:one|a card)(?: of them)? into your hand', after):
            rest = "graveyard" if "put the rest into your graveyard" in after else "bottom"
            n = _n(m.group(1))
            if n > 0:
                effects.append({"type": "impulse", "count": n, "rest": rest})

    m_tutor = re.search(r'search your library for (?:a |an )?([\w\s,]+?) card', text)
    if m_tutor and re.search(r'put (?:it|that card) into your hand', text):
        effects.append({"type": "tutor", "filter": m_tutor.group(1).strip()})

    if re.search(r'return (?:target |a )?(?:[\w\s]+ )?card from your graveyard to your hand', text):
        effects.append({"type": "graveyard_return", "count": 1})

    return effects


def _build_card(data: dict, count: int, ja_name: str = ""):
    """Scryfall API のレスポンス（dict）から Card オブジェクトを生成するヘルパー。

    複数の場所で同じ Card() コンストラクタ呼び出しが繰り返されるのを避けるために
    ここにまとめている（DRY の原則: Don't Repeat Yourself）。

    ja_name: 候補ピッカーから渡された日本語名。
    printed_name がレスポンスにない場合のフォールバックとして使う。
    """
    from deck_builder.deck import Card
    return Card(
        name=data["name"],
        mana_cost=data.get("mana_cost", ""),
        cmc=float(data.get("cmc", 0)),
        colors=data.get("colors", []),
        type_line=data.get("type_line", ""),
        count=count,
        # data に printed_name があればそれを使い、なければ ja_name にフォールバック
        printed_name=data.get("printed_name", "") or ja_name,
        produced_mana=data.get("produced_mana") or [],
    )


# ── ここ以降は Streamlit がページを描画するたびに上から順に実行される ────────────

from deck_builder.deck import Card, Deck
from deck_builder.scryfall import ScryfallClient
from deck_builder.storage import deck_exists, delete_deck, list_decks, load_deck, save_deck

# ページタイトルとレイアウトを設定する（最初に1回だけ呼ぶ）
# layout="wide" : ブラウザの横幅を最大限使う
st.set_page_config(page_title="MTG Deck Builder", layout="wide")


# ── セッションステートの初期化 ─────────────────────────────────────────────────
#
# st.session_state はページが再描画（rerun）されても値が消えない辞書。
# ボタンを押したり入力したりすると Streamlit はスクリプト全体を再実行するが、
# session_state に保存した値はそのまま残る。
#
# "キー not in st.session_state" で初回だけ初期値をセットする。

if "selected_deck" not in st.session_state:
    st.session_state.selected_deck = None      # 現在選択中のデッキ名

if "_candidates" not in st.session_state:
    st.session_state._candidates = []          # 単一カード追加の候補リスト

if "_pending_count" not in st.session_state:
    st.session_state._pending_count = 4        # 候補選択時に使う枚数

if "_pending_target" not in st.session_state:
    st.session_state._pending_target = "メイン"  # 候補選択時の追加先（メイン or SB）

if "_paste_pending" not in st.session_state:
    st.session_state._paste_pending = []       # 貼り付け追加の複数候補リスト

if "_sim_hand" not in st.session_state:
    st.session_state._sim_hand = []            # 現在の手札
if "_sim_library" not in st.session_state:
    st.session_state._sim_library = []         # 残りライブラリ
if "_sim_turn" not in st.session_state:
    st.session_state._sim_turn = 0             # 現在のターン数（0 = 未開始）
if "_sim_mulligans" not in st.session_state:
    st.session_state._sim_mulligans = 0        # マリガン回数
if "_sim_drawn" not in st.session_state:
    st.session_state._sim_drawn = None         # 直前のドローカード（ハイライト用）
if "_sim_deck_name" not in st.session_state:
    st.session_state._sim_deck_name = None     # デッキ変更検知用
if "_sim_battlefield" not in st.session_state:
    st.session_state._sim_battlefield = []     # 戦場に出ている土地
if "_sim_graveyard" not in st.session_state:
    st.session_state._sim_graveyard = []       # 墓地（唱えたスペル・捨てたカード）
if "_sim_lands_played" not in st.session_state:
    st.session_state._sim_lands_played = 0     # このターンに出した土地数（最大1）
if "_sim_tapped" not in st.session_state:
    st.session_state._sim_tapped = []          # battlefield と同インデックスのタップ状態
if "_sim_pending_effects" not in st.session_state:
    st.session_state._sim_pending_effects = [] # 未解決エフェクト
if "_sim_alt_cast_card" not in st.session_state:
    st.session_state._sim_alt_cast_card = None # 代替コストで唱えるカード
if "_sim_mana_pool" not in st.session_state:
    st.session_state._sim_mana_pool = {}       # フローティングマナ {色文字: 枚数}


def reload_deck_list():
    """デッキ名リストキャッシュを最新の状態に更新する。
    デッキを作成・削除した後に呼ぶ。
    """
    st.session_state._deck_names = list_decks()


if "_deck_names" not in st.session_state:
    st.session_state._deck_names = list_decks()  # 起動時にデッキ名を読み込む


# ── サイドバー（左側のパネル）────────────────────────────────────────────────

with st.sidebar:
    st.title("MTG Deck Builder")

    deck_names = st.session_state._deck_names

    if deck_names:
        # st.radio(): 選択肢の中から1つを選ぶウィジェット
        selected = st.radio(
            "デッキ一覧",
            deck_names,
            # 現在のデッキが一覧にあればそのインデックスを初期値にする
            index=deck_names.index(st.session_state.selected_deck)
            if st.session_state.selected_deck in deck_names
            else 0,
        )
        st.session_state.selected_deck = selected
    else:
        st.info("デッキがありません。新規作成してください。")

    st.divider()  # 区切り線

    # デッキ削除ボタン（ポップオーバーで確認画面を出す）
    if st.session_state.selected_deck:
        # st.popover(): クリックすると小さなオーバーレイが開くウィジェット
        with st.popover("🗑 デッキを削除", use_container_width=True):
            st.warning(f"「{st.session_state.selected_deck}」を削除しますか？この操作は元に戻せません。")
            if st.button("削除する", key="confirm_delete_deck"):
                delete_deck(st.session_state.selected_deck)
                st.session_state.selected_deck = None
                reload_deck_list()
                st.rerun()  # ページを再描画して最新状態を反映

    # 新規デッキ作成（折りたたみパネル）
    # st.expander(): クリックで開閉できるパネル
    with st.expander("+ 新規デッキ作成"):
        new_name = st.text_input("デッキ名", key="new_deck_name")
        new_format = st.text_input("フォーマット", key="new_deck_format")
        if st.button("デッキ作成"):
            if not new_name.strip():
                st.error("デッキ名を入力してください。")
            elif deck_exists(new_name):
                st.error(f"'{new_name}' はすでに存在します。")
            else:
                deck = Deck(new_name.strip(), new_format.strip())
                save_deck(deck)
                # ファイル名と同じ形式（小文字・アンダースコア）でセット
                st.session_state.selected_deck = new_name.lower().replace(" ", "_")
                reload_deck_list()
                st.rerun()


# ── メインエリア ──────────────────────────────────────────────────────────────

selected_deck_name = st.session_state.selected_deck

# デッキが選択されていなければここで処理を停止する
if not selected_deck_name:
    st.info("サイドバーからデッキを選択するか、新規作成してください。")
    st.stop()  # st.stop(): 以降のコードを実行しない

# デッキファイルを読み込む
try:
    deck = load_deck(selected_deck_name)
except FileNotFoundError:
    st.error(f"デッキ '{selected_deck_name}' が見つかりません。")
    st.stop()


# ── 日本語名バックフィル ──────────────────────────────────────────────────────
#
# 古いキャッシュで保存されたカードは printed_name が空の場合がある。
# その場合、Scryfall から取り直して補完し、デッキを再保存する。
# （Adventure/DFC カードの日本語名修正対応）

_missing_ja = [c for c in deck.list_cards() + deck.list_sideboard() if not c.printed_name]
if _missing_ja:
    _client = ScryfallClient()
    _updated = False
    for _c in _missing_ja:
        try:
            _data = _client.get_card(_c.name)
            if _data.get("printed_name"):
                _c.printed_name = _data["printed_name"]
                _updated = True
        except Exception:
            pass  # 1枚失敗しても他のカードの処理を続ける
    if _updated:
        save_deck(deck)
        st.rerun()


# デッキ名とフォーマットを表示
st.title(deck.name)
if deck.format:
    st.caption(f"[{deck.format}]")

# ── 2タブ構成 ─────────────────────────────────────────────────────────────────
# st.tabs(): タブを作る。with ブロックで中身を書く。
tab_cards, tab_analyze, tab_simulate = st.tabs(["Cards", "Analyze", "Simulate"])


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Cards タブ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_cards:

    # ── カード追加フォーム ──────────────────────────────────────────────────
    # st.columns([3,1,1,1]) : 横幅比率 3:1:1:1 で4列に分割
    col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
    with col1:
        card_name_input = st.text_input("カード名", key="card_name_input")
    with col2:
        card_count_input = st.number_input("枚数", min_value=1, max_value=99, value=4, key="card_count_input")
    with col3:
        # horizontal=True : ラジオボタンを横並びにする
        add_target = st.radio("追加先", ["メイン", "SB"], horizontal=True, key="add_target")
    with col4:
        st.write("")   # 縦位置を合わせるための空白
        st.write("")
        add_clicked = st.button("Scryfallから追加")

    # ボタンが押されたときの処理
    if add_clicked:
        if not card_name_input.strip():
            st.error("カード名を入力してください。")
        else:
            # 新しい検索のたびに前回の候補をクリアする
            st.session_state._candidates = []
            with st.spinner("取得中..."):
                try:
                    client = ScryfallClient()
                    # ── カード追加フロー ──────────────────────────────────────
                    # 1. get_card_exact() で完全一致を試みる
                    # 2. ValueError（404）なら search_candidates() で候補一覧を取得
                    # 3. 候補1件 → 自動追加
                    # 4. 複数件 → session_state に格納してピッカー UI 表示
                    # ─────────────────────────────────────────────────────────
                    try:
                        data = client.get_card_exact(card_name_input.strip())
                        card = _build_card(data, int(card_count_input))
                        if add_target == "SB":
                            deck.add_sideboard_card(card, int(card_count_input))
                        else:
                            deck.add_card(card, int(card_count_input))
                        save_deck(deck)
                        st.success(f"{card.display_name()} x{int(card_count_input)} を追加しました。")
                        st.rerun()
                    except ValueError:
                        # 完全一致で見つからなかった → 候補検索（完全一致が先頭に来るよう内部でソート済み）
                        candidates = client.search_candidates(card_name_input.strip())
                        if not candidates:
                            st.error("カードが見つかりませんでした。")
                        elif len(candidates) == 1:
                            # 候補が1件だけなら自動で追加する
                            data = client.get_card(candidates[0]["en_name"])
                            card = _build_card(data, int(card_count_input), candidates[0].get("ja_name", ""))
                            if add_target == "SB":
                                deck.add_sideboard_card(card, int(card_count_input))
                            else:
                                deck.add_card(card, int(card_count_input))
                            save_deck(deck)
                            st.success(f"{card.display_name()} x{int(card_count_input)} を追加しました。")
                            st.rerun()
                        else:
                            # 複数候補 → session_state に保存してピッカーを表示
                            st.session_state._candidates = candidates
                            st.session_state._pending_count = int(card_count_input)
                            st.session_state._pending_target = add_target
                except Exception as e:
                    st.error(f"カードの取得に失敗しました: {e}")

    # ── 候補ピッカー UI ──────────────────────────────────────────────────────
    # session_state._candidates が空でなければ表示する
    if st.session_state._candidates:
        st.warning(f"{len(st.session_state._candidates)} 件の候補が見つかりました。追加するカードを選択してください:")
        # ヘッダー行
        hdr = st.columns([4, 3, 2, 1])
        hdr[0].markdown("**カード名**")
        hdr[1].markdown("**タイプ**")
        hdr[2].markdown("**マナコスト**")
        for cand in st.session_state._candidates:
            row = st.columns([4, 3, 2, 1])
            # 日本語名があれば "稲妻/Lightning Bolt" 形式で表示
            display = cand["en_name"]
            if cand.get("ja_name"):
                display = f"{cand['ja_name']}/{display}"
            row[0].write(display)
            row[1].write(cand.get("type_line", ""))
            row[2].write(cand.get("mana_cost", ""))
            # key に en_name を含めることで各行のボタンを区別する
            if row[3].button("追加", key=f"cand_{cand['en_name']}"):
                try:
                    client = ScryfallClient()
                    data = client.get_card(cand["en_name"])
                    card = _build_card(data, st.session_state._pending_count, cand.get("ja_name", ""))
                    if st.session_state._pending_target == "SB":
                        deck.add_sideboard_card(card, st.session_state._pending_count)
                    else:
                        deck.add_card(card, st.session_state._pending_count)
                    save_deck(deck)
                    st.session_state._candidates = []  # 候補をクリア
                    st.rerun()
                except Exception as e:
                    st.error(f"カードの取得に失敗しました: {e}")
        if st.button("キャンセル", key="cancel_candidates"):
            st.session_state._candidates = []
            st.rerun()

    # ── テキスト貼り付け一括追加 ─────────────────────────────────────────────
    st.divider()
    with st.expander("テキスト貼り付けで一括追加"):
        paste_target = st.radio("追加先", ["メイン", "SB"], horizontal=True, key="paste_target")
        paste_text = st.text_area(
            "デッキリストを貼り付け（1行1カード）",
            placeholder="4 Lightning Bolt\n3x Counterspell\nエッジウォール    4\n苔森    2",
            height=200,
            key="paste_input",
        )
        if st.button("一括追加", key="paste_import"):
            # 1. テキストを行に分割し、空行とコメント行（// で始まる）を除く
            lines = [l.strip() for l in paste_text.splitlines()
                     if l.strip() and not l.strip().startswith("//")]

            parsed = []       # パースできた行: [(枚数, カード名, 追加先), ...]
            parse_errors = [] # パースできなかった行
            current_target = paste_target  # 現在の追加先（サイドボードマーカーで変わる）

            # "Sideboard" "サイドボード" "サイド" 等の行を検出する正規表現
            # re.IGNORECASE: 大文字小文字を区別しない
            _sb_marker = re.compile(r'^(sideboard|サイドボード|サイド|side\s*board)$', re.IGNORECASE)

            for line in lines:
                # サイドボードマーカー行を検出したら追加先を切り替える
                if _sb_marker.match(line):
                    current_target = "SB"
                    continue

                # パターン1: "4 Lightning Bolt" または "4x Lightning Bolt"
                m = re.match(r'^(\d+)[xX]?\s+(.+)$', line)
                if m:
                    parsed.append((int(m.group(1)), m.group(2).strip(), current_target))
                else:
                    # パターン2: "Lightning Bolt 4"（カード名が先、枚数が後）
                    m2 = re.match(r'^(.+?)\s+(\d+)$', line)
                    if m2:
                        parsed.append((int(m2.group(2)), m2.group(1).strip(), current_target))
                    else:
                        parse_errors.append(line)

            if parse_errors:
                st.warning("パースできなかった行:\n" + "\n".join(parse_errors))

            if parsed:
                client = ScryfallClient()
                # st.progress(): 0〜1 の進捗バー（1 で完了）
                progress = st.progress(0)
                errors = []    # 取得失敗カードのメッセージ
                pending = []   # 複数候補があって保留になったカード
                added_count = 0

                for i, (count, name, target) in enumerate(parsed):
                    try:
                        try:
                            data = client.get_card_exact(name)
                            card = _build_card(data, count)
                        except ValueError:
                            candidates = client.search_candidates(name)
                            if not candidates:
                                errors.append(f"{name}: カードが見つかりませんでした")
                                progress.progress((i + 1) / len(parsed))
                                continue
                            elif len(candidates) == 1:
                                data = client.get_card(candidates[0]["en_name"])
                                card = _build_card(data, count, candidates[0].get("ja_name", ""))
                            else:
                                # 複数候補 → 後でユーザーに選ばせる
                                pending.append({"name": name, "count": count, "candidates": candidates, "target": target})
                                progress.progress((i + 1) / len(parsed))
                                continue
                        if target == "SB":
                            deck.add_sideboard_card(card, count)
                        else:
                            deck.add_card(card, count)
                        added_count += 1
                    except Exception as e:
                        errors.append(f"{name}: {e}")
                    progress.progress((i + 1) / len(parsed))

                save_deck(deck)
                st.session_state._paste_pending = pending

                if errors:
                    st.warning("一部のカードが取得できませんでした:\n" + "\n".join(errors))
                if added_count > 0:
                    st.success(f"{added_count} 種類のカードを追加しました。")
                if not pending:
                    st.rerun()

    # ── 貼り付け保留の複数候補ピッカー ──────────────────────────────────────
    if st.session_state._paste_pending:
        st.warning(f"{len(st.session_state._paste_pending)} 件のカードに複数候補があります。追加するカードを選択してください:")
        for item in st.session_state._paste_pending:
            st.markdown(f"**{item['name']}** (x{item['count']}) → {'SB' if item.get('target') == 'SB' else 'メイン'}")
            hdr = st.columns([4, 3, 2, 1])
            hdr[0].markdown("カード名")
            hdr[1].markdown("タイプ")
            hdr[2].markdown("マナコスト")
            for cand in item["candidates"]:
                row = st.columns([4, 3, 2, 1])
                display = cand["en_name"]
                if cand.get("ja_name"):
                    display = f"{cand['ja_name']}/{display}"
                row[0].write(display)
                row[1].write(cand.get("type_line", ""))
                row[2].write(cand.get("mana_cost", ""))
                # キーにカード名と候補名を両方入れることで同名ボタンの衝突を避ける
                if row[3].button("追加", key=f"paste_cand_{item['name']}_{cand['en_name']}"):
                    try:
                        client = ScryfallClient()
                        data = client.get_card(cand["en_name"])
                        card = _build_card(data, item["count"], cand.get("ja_name", ""))
                        if item.get("target") == "SB":
                            deck.add_sideboard_card(card, item["count"])
                        else:
                            deck.add_card(card, item["count"])
                        save_deck(deck)
                        # 選択済みのカードを保留リストから除去する
                        st.session_state._paste_pending = [
                            p for p in st.session_state._paste_pending if p["name"] != item["name"]
                        ]
                        st.rerun()
                    except Exception as e:
                        st.error(f"カードの取得に失敗しました: {e}")
        if st.button("キャンセル", key="cancel_paste_pending"):
            st.session_state._paste_pending = []
            st.rerun()

    # ── CSV エクスポート ───────────────────────────────────────────────────
    st.divider()

    def _deck_to_csv(deck) -> str:
        """デッキの内容を CSV 文字列に変換する。

        フォーマット:
          count,name,japanese_name,type,mana_cost,cmc
        メインデッキを出力した後、サイドボードがあれば
        "Sideboard" 行を挟んでサイドボードのカードを続ける。
        （このフォーマットはテキスト貼り付け一括追加とも互換性がある）
        """
        lines = ["count,name,japanese_name,type,mana_cost,cmc"]
        for card in deck.list_cards():
            lines.append(
                f"{card.count},{card.name},{card.printed_name},"
                f"{primary_type(card.type_line)},{card.mana_cost},{int(card.cmc)}"
            )
        if deck.list_sideboard():
            lines.append("Sideboard")
            for card in deck.list_sideboard():
                lines.append(
                    f"{card.count},{card.name},{card.printed_name},"
                    f"{primary_type(card.type_line)},{card.mana_cost},{int(card.cmc)}"
                )
        return "\n".join(lines)

    csv_data = _deck_to_csv(deck)
    # st.download_button(): クリックするとファイルをダウンロードするボタン
    # data    : ダウンロードするデータ（文字列またはバイト列）
    # file_name: 保存時のファイル名
    # mime    : ファイルの種類（CSV は "text/csv"）
    st.download_button(
        label="📥 CSVをダウンロード",
        data=csv_data.encode("utf-8-sig"),  # BOM付きUTF-8（Excelで文字化けしない）
        file_name=f"{selected_deck_name}.csv",
        mime="text/csv",
    )

    # ── CSV インポート ─────────────────────────────────────────────────────
    st.divider()
    # st.file_uploader(): ファイルをアップロードするウィジェット
    # type=["csv"] : CSV ファイルのみ受け付ける
    uploaded = st.file_uploader("CSVからインポート (count,name)", type="csv", key="csv_uploader")
    if uploaded is not None:
        try:
            # decode("utf-8-sig"): BOM 付き UTF-8（Excel が吐く形式）にも対応
            content = uploaded.read().decode("utf-8-sig")
            # csv.DictReader: ヘッダー行をキーとして各行を辞書として読む
            reader = csv.DictReader(io.StringIO(content))
            rows = [r for r in reader if r.get("name", "").strip()]
        except Exception as e:
            st.error(f"CSVの読み込みに失敗しました: {e}")
            rows = []

        if rows:
            st.write(f"{len(rows)} 件のカードを検出しました。")
            if st.button("インポート実行"):
                client = ScryfallClient()
                progress = st.progress(0)
                errors = []
                for i, row in enumerate(rows):
                    name = row["name"].strip()
                    count = int(row.get("count", 1) or 1)
                    try:
                        data = client.get_card(name)
                        card = _build_card(data, count)
                        deck.add_card(card, count)
                    except Exception as e:
                        errors.append(f"{name}: {e}")
                    progress.progress((i + 1) / len(rows))
                save_deck(deck)
                if errors:
                    st.warning("一部のカードが取得できませんでした:\n" + "\n".join(errors))
                else:
                    st.success(f"{len(rows)} 枚をインポートしました。")
                st.rerun()

    # ── カード一覧テーブル ────────────────────────────────────────────────────
    st.divider()

    # 枚数 number_input: ネイティブスピナーを常時表示・コンパクト化
    st.markdown("""
    <style>
    input[data-testid="stNumberInputField"] {
        text-align: center;
        font-size: 0.85rem;
    }
    input[data-testid="stNumberInputField"]::-webkit-inner-spin-button,
    input[data-testid="stNumberInputField"]::-webkit-outer-spin-button {
        -webkit-appearance: auto !important;
        opacity: 1 !important;
        cursor: pointer;
    }
    </style>
    """, unsafe_allow_html=True)

    # テーブルの列幅比率 [枚数, カード名, タイプ, マナコスト, CMC, 移動, 削除]
    _CARD_COLS = [4, 5, 2, 2, 1, 1, 1]

    def _card_table_header(container):
        """テーブルのヘッダー行を描画する。container は st の列（column）オブジェクト。"""
        h = container.columns(_CARD_COLS)
        h[0].markdown("**枚数**")
        h[1].markdown("**カード名**")
        h[2].markdown("**タイプ**")
        h[3].markdown("**マナコスト**")
        h[4].markdown("**CMC**")
        h[5].markdown("**移動**")
        # h[6] は削除ボタン用（ヘッダーなし）

    def _render_card_rows(container, cards, move_btn_label, move_fn, del_fn, set_count_fn, key_prefix):
        """カード一覧の各行を描画する。

        container     : 描画先の Streamlit コンテナ（列など）
        cards         : 表示するカードのリスト
        move_btn_label: 移動ボタンのラベル（"↓" or "↑"）
        move_fn       : 移動ボタンを押したときに呼ぶ関数（deck.move_to_sideboard など）
        del_fn        : 削除ボタンを押したときに呼ぶ関数（deck.remove_card など）
        set_count_fn  : 枚数変更関数（deck.set_card_count など）
        key_prefix    : 各ボタンのキーの接頭辞（同名ボタンの衝突を避ける）
        """
        if not cards:
            container.info("カードがありません。")
            return
        _card_table_header(container)
        for card in cards:
            rc = container.columns(_CARD_COLS)
            # rc[0]: number_input（MTG プロジェクトの <input type="number"> と同等）
            count_key = f"{st.session_state.get('selected_deck', '')}_{key_prefix}_cnt_{card.name}"
            new_count = rc[0].number_input(
                "枚数",
                min_value=1, max_value=99,
                value=card.count,
                step=1,
                key=count_key,
                label_visibility="collapsed",
            )
            if new_count != card.count:
                set_count_fn(card.name, new_count)
                save_deck(deck)
                st.rerun()
            rc[1].write(card.display_name())
            rc[2].write(primary_type(card.type_line))
            rc[3].write(card.mana_cost)
            rc[4].write(int(card.cmc))
            if rc[5].button(move_btn_label, key=f"{key_prefix}_mv_{card.name}"):
                move_fn(card.name)
                save_deck(deck)
                st.rerun()
            if rc[6].button("×", key=f"{key_prefix}_del_{card.name}"):
                del_fn(card.name)
                save_deck(deck)
                st.rerun()

    def _render_section(cards, title, clear_key, clear_fn, move_btn_label, move_fn, del_fn, set_count_fn, key_prefix):
        """メインデッキ or サイドボードの1セクションを描画する。

        タイトル・全削除ボタン・2列レイアウト（スペル | 土地）で構成される。
        """
        # タイトルと「全削除」ポップオーバーを横並びに配置
        hc1, hc2 = st.columns([6, 1])
        hc1.markdown(title)
        with hc2.popover("全削除"):
            st.warning(f"{title} のカードをすべて削除しますか？")
            if st.button("削除する", key=f"confirm_{clear_key}"):
                clear_fn()
                save_deck(deck)
                st.rerun()

        # type_line に "Land" が含まれるかどうかでスペルと土地を分ける
        spells = [c for c in cards if "Land" not in c.type_line]
        lands  = [c for c in cards if "Land"     in c.type_line]

        # st.columns(2): 等幅2列に分割
        left, right = st.columns(2)
        with left:
            st.caption(f"スペル（{sum(c.count for c in spells)}枚）")
            _render_card_rows(left, spells, move_btn_label, move_fn, del_fn, set_count_fn, key_prefix + "_sp")
        with right:
            st.caption(f"土地（{sum(c.count for c in lands)}枚）")
            _render_card_rows(right, lands, move_btn_label, move_fn, del_fn, set_count_fn, key_prefix + "_ld")

    # メインデッキとサイドボードを描画する
    main_cards = deck.list_cards()
    sb_cards   = deck.list_sideboard()

    _render_section(
        main_cards,
        f"#### メインデッキ（{deck.total_cards()}枚）",
        "clear_main", deck.clear_cards,
        "↓",           # メインデッキのカードはサイドへ移動（↓）
        deck.move_to_sideboard, deck.remove_card,
        deck.set_card_count,
        "main",
    )

    _render_section(
        sb_cards,
        f"#### サイドボード（{deck.total_sideboard()}枚）",
        "clear_sb", deck.clear_sideboard,
        "↑",          # サイドボードのカードはメインへ移動（↑）
        deck.move_to_main, deck.remove_sideboard_card,
        deck.set_sideboard_count,
        "sb",
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Analyze タブ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_analyze:
    # altair: 宣言的グラフライブラリ。グラフの「見た目」をコードで記述する。
    #   .interactive() を呼ばないことでスクロールによるズームを無効化できる。
    # pandas: 表形式データ（DataFrame）を扱うライブラリ。Altair への入力に使う。
    import altair as alt
    import pandas as pd

    def _bar(data: dict, height: int = 260) -> alt.Chart:
        """辞書から Altair の棒グラフを生成するヘルパー。

        data   : {ラベル: 値} の辞書（例: {"1": 8, "2": 12, "3": 4}）
        height : グラフの高さ（ピクセル）
        """
        # pd.DataFrame(): 辞書から表を作る
        # {"key": [...], "val": [...]} → 2列の表
        df = pd.DataFrame({"key": list(data.keys()), "val": list(data.values())})
        return (
            alt.Chart(df)
            .mark_bar()   # 棒グラフ
            .encode(
                # X 軸: key 列（:N は名義変数 = カテゴリ）、sort=None で入力順を維持
                x=alt.X("key:N", sort=None, axis=alt.Axis(labelAngle=-30), title=""),
                # Y 軸: val 列（:Q は定量変数 = 数値）
                y=alt.Y("val:Q", title=""),
            )
            .properties(height=height)
            # ※ .interactive() を呼ばないことでスクロールズームを無効にしている
        )

    # ── 基本統計メトリクス ──────────────────────────────────────────────────
    stats = deck_stats(deck)
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Cards", stats["total"])
    m2.metric("Unique Cards", stats["unique"])
    m3.metric("Avg CMC", stats["avg_cmc"])

    # 色コード → 表示名の対応表
    color_names      = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
    color_names_full = {
        "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green",
        "C": "Colorless", "Any": "Any", "All": "All",
    }
    ls = land_stats(deck)

    # ── 各色を引いている確率（折れ線グラフ・全幅・最上段）───────────────────
    probs = color_probability_by_turn(deck)
    if probs:
        # max_turn: どの色のリストも同じ長さ（デフォルト6）なので最初の値から取る
        max_turn = len(next(iter(probs.values())))

        # DataFrame を作る。
        # index: ["T1", "T2", ..., "T6"]
        # 列:   色名 → 確率リスト
        df_prob = pd.DataFrame(
            {color_names_full.get(c, c): vals for c, vals in probs.items()},
            index=[f"T{t}" for t in range(1, max_turn + 1)],
        )

        # Altair は「横持ち（ wide）」より「縦持ち（long/tidy）」が得意。
        # melt() で列を "Color" 変数として縦に変換する。
        # 変換前: Turn | White | Blue | ...
        # 変換後: Turn | Color  | Probability
        df_long = (
            df_prob.reset_index()
            .melt(id_vars="index", var_name="Color", value_name="Probability")
            .rename(columns={"index": "Turn"})
        )

        line_chart = (
            alt.Chart(df_long)
            .mark_line(point=True)  # 折れ線グラフ（各点にドットを表示）
            .encode(
                x=alt.X("Turn:N", title=""),
                y=alt.Y("Probability:Q", scale=alt.Scale(domain=[0, 100]), title="%"),
                color=alt.Color("Color:N"),  # 色ごとに線の色を自動で変える
            )
            .properties(height=300)
        )
        st.subheader("各色を引いている確率（%）")
        st.caption("Opening hand 7枚 + ターンNのドローまでに該当色の土地を1枚以上引いている確率")
        # use_container_width=True: グラフをコンテナ幅いっぱいに広げる
        st.altair_chart(line_chart, use_container_width=True)

    # ── 1段目: マナカーブ | カードタイプ分布 ─────────────────────────────────
    r1c1, r1c2 = st.columns(2)
    with r1c1:
        st.subheader("マナカーブ")
        curve = mana_curve(deck)
        if curve:
            # CMC（整数）をキーにするため str() で文字列に変換
            st.altair_chart(_bar({str(k): v for k, v in curve.items()}), use_container_width=True)
        else:
            st.info("データがありません。")
    with r1c2:
        st.subheader("カードタイプ分布")
        type_dist = type_distribution(deck)
        if type_dist:
            st.altair_chart(_bar(type_dist), use_container_width=True)
        else:
            st.info("データがありません。")

    # ── 2段目: 色分布 | 土地マナ生成 ────────────────────────────────────────
    # 土地のメトリクス（枚数・比率）を2列の外に出すことで、
    # 色分布グラフとマナ生成グラフの高さを揃えている。
    st.subheader("土地")
    lc1, lc2 = st.columns(2)
    lc1.metric("土地枚数", ls["land_count"])
    lc2.metric("土地比率", f"{ls['ratio']}%")

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        st.markdown("**色分布**")
        dist = color_distribution(deck)
        if dist:
            # 色コード → 英語名に変換してから表示
            dist_named = {color_names.get(k, k): v for k, v in dist.items()}
            st.altair_chart(_bar(dist_named), use_container_width=True)
        else:
            st.info("データがありません。")
    with r2c2:
        st.markdown("**マナ生成分布**")
        if ls["produced_mana"]:
            pm_named = {color_names_full.get(k, k): v for k, v in ls["produced_mana"].items()}
            st.altair_chart(_bar(pm_named), use_container_width=True)
        elif ls["land_count"] > 0:
            st.info("土地のマナ生成情報がありません（カードを再取得すると表示されます）。")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Simulate タブ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
with tab_simulate:

    # デッキが切り替わったらシミュレーション状態をリセットする
    if st.session_state._sim_deck_name != selected_deck_name:
        st.session_state._sim_hand = []
        st.session_state._sim_library = []
        st.session_state._sim_turn = 0
        st.session_state._sim_mulligans = 0
        st.session_state._sim_drawn = None
        st.session_state._sim_deck_name = selected_deck_name
        st.session_state._sim_battlefield = []
        st.session_state._sim_graveyard = []
        st.session_state._sim_lands_played = 0
        st.session_state._sim_tapped = []
        st.session_state._sim_pending_effects = []
        st.session_state._sim_alt_cast_card = None
        st.session_state._sim_mana_pool = {}

    _sim_initialized = bool(st.session_state._sim_hand or st.session_state._sim_turn > 0)

    if not _sim_initialized:
        st.info("シャッフルして初手7枚を引くと、マリガンやターン進行をシミュレートできます。")
        if deck.total_cards() == 0:
            st.warning("デッキにカードがありません。")
        elif st.button("🔀 シャッフルして初手を引く", key="sim_start"):
            flat = _build_flat_deck(deck)
            st.session_state._sim_hand = flat[:7]
            st.session_state._sim_library = flat[7:]
            st.session_state._sim_turn = 1
            st.session_state._sim_mulligans = 0
            st.session_state._sim_drawn = None
            st.session_state._sim_battlefield = []
            st.session_state._sim_graveyard = []
            st.session_state._sim_lands_played = 0
            st.session_state._sim_tapped = []
            st.session_state._sim_pending_effects = []
            st.session_state._sim_alt_cast_card = None
            st.session_state._sim_mana_pool = {}
            st.rerun()
    else:
        _hand         = st.session_state._sim_hand
        _library      = st.session_state._sim_library
        _turn         = st.session_state._sim_turn
        _mulligans    = st.session_state._sim_mulligans
        _drawn        = st.session_state._sim_drawn
        _battlefield  = st.session_state._sim_battlefield
        _graveyard    = st.session_state._sim_graveyard
        _lands_played = st.session_state._sim_lands_played
        _tapped       = st.session_state._sim_tapped
        _pending      = st.session_state._sim_pending_effects
        _alt_cast_card = st.session_state._sim_alt_cast_card
        # alt_cast_card が手札にない場合は自動クリア
        if _alt_cast_card is not None and not any(c is _alt_cast_card for c in _hand):
            st.session_state._sim_alt_cast_card = None
            _alt_cast_card = None
        _alt_casting = (_alt_cast_card is not None) and not _pending

        def _execute_cast(card, tap_indices, pool_after=None):
            """スペルを唱える共通処理（通常・代替コスト共用）。"""
            _new_tapped = list(_tapped)
            for _ti in tap_indices:
                _new_tapped[_ti] = True
            _new_hand = []
            _done = False
            for c in _hand:
                if c is card and not _done:
                    _done = True
                    continue
                _new_hand.append(c)
            # パーマネント（クリーチャー/アーティファクト/エンチャント/PW）は戦場へ
            _PERM_TYPES = {"Creature", "Artifact", "Enchantment", "Planeswalker"}
            if card.get("type") in _PERM_TYPES:
                _entering = dict(card)
                _entering["entered_turn"] = _turn
                _new_battlefield = list(_battlefield) + [_entering]
                _new_tapped.append(False)
                _new_graveyard = list(_graveyard)
            else:
                _new_battlefield = list(_battlefield)
                _new_graveyard = list(_graveyard) + [card]
            _effs = _detect_hand_effects(card.get("oracle_text", ""))
            _new_library = list(_library)
            _new_drawn = _drawn if any(c is _drawn for c in _new_hand) else None
            for _e in _effs:
                if _e["type"] == "draw":
                    for _ in range(_e["count"]):
                        if _new_library:
                            _d = _new_library.pop(0)
                            _new_hand.append(_d)
                            _new_drawn = _d
            _new_pending = list(_pending)
            for _e in _effs:
                _et = _e["type"]
                if _et in ("discard", "return", "rummage"):
                    _new_pending.append(_e)
                elif _et == "mill":
                    for _ in range(_e["count"]):
                        if _new_library:
                            _new_graveyard.append(_new_library.pop(0))
                elif _et in ("scry", "surveil"):
                    _extracted = []
                    for _ in range(_e["count"]):
                        if _new_library:
                            _extracted.append(_new_library.pop(0))
                    if _extracted:
                        _new_pending.append({"type": _et, "cards": _extracted})
                elif _et == "impulse":
                    _extracted = []
                    for _ in range(_e["count"]):
                        if _new_library:
                            _extracted.append(_new_library.pop(0))
                    if _extracted:
                        _new_pending.append({"type": "impulse", "cards": _extracted, "rest": _e.get("rest", "bottom")})
                elif _et in ("tutor", "graveyard_return"):
                    _new_pending.append(_e)
                elif _et == "token":
                    _tok_base = {
                        "name": _e["desc"] + " Token",
                        "display": f"🪙 {_e['desc']}",
                        "mana_cost": "", "cmc": 0,
                        "type": "Creature", "type_line": "Token Creature",
                        "produced_mana": [], "oracle_text": "", "is_token": True,
                        "entered_turn": _turn,
                    }
                    for _ in range(_e["count"]):
                        _new_battlefield.append(dict(_tok_base))
                        _new_tapped.append(False)
            st.session_state._sim_hand = _new_hand
            st.session_state._sim_library = _new_library
            st.session_state._sim_graveyard = _new_graveyard
            st.session_state._sim_battlefield = _new_battlefield
            st.session_state._sim_tapped = _new_tapped
            st.session_state._sim_pending_effects = _new_pending
            st.session_state._sim_drawn = _new_drawn
            st.session_state._sim_alt_cast_card = None
            st.session_state._sim_mana_pool = pool_after if pool_after is not None else {}
            st.rerun()

        def _play_land(card, tapped: bool, extra_pending: list | None = None, new_library: list | None = None):
            """土地を戦場に出す共通処理（フェッチ以外）。"""
            _new_hand = [c for c in _hand if c is not card]
            if card is _drawn:
                st.session_state._sim_drawn = None
            st.session_state._sim_hand = _new_hand
            st.session_state._sim_lands_played = _lands_played + 1
            st.session_state._sim_battlefield = list(_battlefield) + [card]
            st.session_state._sim_tapped = list(_tapped) + [tapped]
            st.session_state._sim_pending_effects = list(_pending) + (extra_pending or [])
            if new_library is not None:
                st.session_state._sim_library = new_library
            st.rerun()

        def _play_fetchland(card):
            """フェッチランドをプレイ（墓地へ → fetch pending）。"""
            _new_hand = [c for c in _hand if c is not card]
            if card is _drawn:
                st.session_state._sim_drawn = None
            st.session_state._sim_hand = _new_hand
            st.session_state._sim_lands_played = _lands_played + 1
            st.session_state._sim_graveyard = list(_graveyard) + [card]
            _filt = _fetch_filter(card.get("oracle_text", ""))
            st.session_state._sim_pending_effects = list(_pending) + [{"type": "fetch", "filter": _filt}]
            st.rerun()

        # _tapped リストが battlefield と長さが合わない場合（古い state との互換）は補正
        if len(_tapped) != len(_battlefield):
            _tapped = [False] * len(_battlefield)
            st.session_state._sim_tapped = _tapped

        _untapped_count = sum(1 for t in _tapped if not t)
        _em_map = {"W": "⬜", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢", "C": "⬛"}

        # ── メトリクス行（5列）──────────────────────────────────────────────
        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("ターン", _turn)
        sm2.metric("手札", len(_hand))
        sm3.metric("ライブラリ", len(_library))
        sm4.metric("未タップ土地", _untapped_count)
        sm5.metric("マリガン", _mulligans)
        _cur_mana_pool = st.session_state.get("_sim_mana_pool", {})
        if _cur_mana_pool:
            _pool_str = "  ".join(
                "".join([_em_map.get(c, c)] * n) for c, n in _cur_mana_pool.items()
            )
            st.caption(f"🔮 フローティングマナ: {_pool_str}")

        # ── 戦場（土地 + 非土地パーマネント）────────────────────────────
        _lands_bf  = [(i, c) for i, c in enumerate(_battlefield) if c.get("type") == "Land"]
        _perms_bf  = [(i, c) for i, c in enumerate(_battlefield) if c.get("type") != "Land"]
        if _lands_bf:
            _land_parts = []
            for _li, _ld in _lands_bf:
                _colors_str = "".join(_em_map.get(c, c) for c in _ld.get("produced_mana", []))
                _tap_mark = "⊤" if _tapped[_li] else "◇"
                _land_parts.append(f"{_tap_mark}{_ld['display']}({_colors_str or '?'})")
            st.markdown("🏔 **土地**: " + "  ".join(_land_parts))
        if _perms_bf:
            st.markdown("⚔️ **パーマネント**")
            for _pi, (_perm_i, _perm) in enumerate(_perms_bf):
                _pr = st.columns([4, 2, 2, 1])
                _ptap = "⊤" if _tapped[_perm_i] else "◇"
                _sick = _is_summoning_sick(_perm, _turn)
                _sick_mark = " 😴" if _sick else ""
                _pr[0].write(f"{_ptap} {_perm['display']}{_sick_mark}")
                _pr[1].write(_perm.get("type", ""))
                _pem_colors = _perm.get("produced_mana", [])
                if _pem_colors and not _tapped[_perm_i]:
                    if _sick:
                        _pr[2].write("召喚酔い")
                    else:
                        _unique_pem_colors = list(dict.fromkeys(_pem_colors))  # 重複除去・順序保持
                        _tap_btns = _pr[2].columns(len(_unique_pem_colors))
                        for _ci, _color in enumerate(_unique_pem_colors):
                            _btn_label = f"🔱{_em_map.get(_color, _color)}"
                            if _tap_btns[_ci].button(_btn_label, key=f"tap_perm_{_perm_i}_{_perm['name']}_{_color}_{_pi}"):
                                _nt = list(_tapped)
                                _nt[_perm_i] = True
                                _np = dict(st.session_state.get("_sim_mana_pool", {}))
                                _np[_color] = _np.get(_color, 0) + 1
                                st.session_state._sim_tapped = _nt
                                st.session_state._sim_mana_pool = _np
                                st.rerun()
                else:
                    _pr[2].write("")
                if _pr[3].button("✖", key=f"rm_perm_{_perm_i}_{_perm['name']}_{_pi}"):
                    _new_bf = [c for j, c in enumerate(_battlefield) if j != _perm_i]
                    _new_t  = [t for j, t in enumerate(_tapped) if j != _perm_i]
                    _new_gy = list(_graveyard) if _perm.get("is_token") else list(_graveyard) + [_perm]
                    st.session_state._sim_battlefield = _new_bf
                    st.session_state._sim_tapped = _new_t
                    st.session_state._sim_graveyard = _new_gy
                    st.rerun()

        st.divider()

        # ── 手札表示 ─────────────────────────────────────────────────────
        if _pending:
            _eff0 = _pending[0]
            _eff0_type = _eff0["type"]

            if _eff0_type in ("discard", "return"):
                _verb = "捨てて" if _eff0_type == "discard" else "ライブラリへ戻して"
                st.warning(f"⚠️ {_eff0['count']}枚{_verb}ください")

            elif _eff0_type in ("scry", "surveil"):
                _sc_cards = _eff0["cards"]
                _sc_label = "Scry" if _eff0_type == "scry" else "Surveil"
                st.info(f"🔮 **{_sc_label}**: カードを確認してください（残り {len(_sc_cards)} 枚）")
                _sc_card = _sc_cards[0]
                if _eff0_type == "surveil":
                    _scc = st.columns([3, 1, 2, 2, 2, 2])
                else:
                    _scc = st.columns([3, 1, 2, 2, 2])
                _scc[0].write(_sc_card["display"])
                _scc[1].write(_sc_card["cmc"])
                _scc[2].write(_sc_card["type"])
                _sc_rest = list(_sc_cards[1:])
                _sc_np = list(_pending)
                if _scc[3].button("▲ トップ", key=f"sc_top_{len(_sc_cards)}"):
                    if _sc_rest:
                        _sc_np[0] = {**_eff0, "cards": _sc_rest}
                    else:
                        _sc_np.pop(0)
                    st.session_state._sim_library = [_sc_card] + list(_library)
                    st.session_state._sim_pending_effects = _sc_np
                    st.rerun()
                if _scc[4].button("▼ 底へ", key=f"sc_bot_{len(_sc_cards)}"):
                    if _sc_rest:
                        _sc_np[0] = {**_eff0, "cards": _sc_rest}
                    else:
                        _sc_np.pop(0)
                    st.session_state._sim_library = list(_library) + [_sc_card]
                    st.session_state._sim_pending_effects = _sc_np
                    st.rerun()
                if _eff0_type == "surveil":
                    if _scc[5].button("🪦 墓地へ", key=f"sc_gy_{len(_sc_cards)}"):
                        if _sc_rest:
                            _sc_np[0] = {**_eff0, "cards": _sc_rest}
                        else:
                            _sc_np.pop(0)
                        st.session_state._sim_graveyard = list(_graveyard) + [_sc_card]
                        st.session_state._sim_pending_effects = _sc_np
                        st.rerun()

            elif _eff0_type == "impulse":
                _imp_cards = _eff0["cards"]
                _imp_rest = _eff0.get("rest", "bottom")
                _rest_label = "底" if _imp_rest == "bottom" else "墓地"
                st.info(f"🔍 **Impulse**: {len(_imp_cards)} 枚から 1 枚を選んでください（残りは{_rest_label}に置かれます）")
                for _ii, _ic in enumerate(_imp_cards):
                    _ir = st.columns([3, 1, 2, 2])
                    _ir[0].write(_ic["display"])
                    _ir[1].write(_ic["cmc"])
                    _ir[2].write(_ic["type"])
                    if _ir[3].button("手札に加える", key=f"imp_{_ii}_{_ic['name']}"):
                        _np = list(_pending)
                        _np.pop(0)
                        _rest_cards = [c for c in _imp_cards if c is not _ic]
                        _nl = list(_library)
                        _ng = list(_graveyard)
                        if _imp_rest == "bottom":
                            _nl = _nl + _rest_cards
                        else:
                            _ng = _ng + _rest_cards
                        st.session_state._sim_hand = list(_hand) + [_ic]
                        st.session_state._sim_library = _nl
                        st.session_state._sim_graveyard = _ng
                        st.session_state._sim_pending_effects = _np
                        st.rerun()

            elif _eff0_type == "tutor":
                _t_filter = _eff0.get("filter", "")
                st.info(f"🔎 **Tutor**: ライブラリから **{_t_filter}** カードを選んでください")
                _t_search = st.text_input("検索", key="tutor_search_box", placeholder="カード名で絞り込む")
                _t_candidates = [
                    c for c in _library
                    if _t_filter.lower() in c.get("type", "").lower()
                    or _t_filter.lower() in c.get("name", "").lower()
                ]
                if _t_search:
                    _t_candidates = [c for c in _t_candidates if _t_search.lower() in c["display"].lower()]
                if not _t_candidates:
                    st.warning("条件に合うカードがライブラリにありません。")
                    if st.button("スキップ", key="tutor_skip"):
                        _np = list(_pending)
                        _np.pop(0)
                        st.session_state._sim_pending_effects = _np
                        st.rerun()
                else:
                    for _ti, _tc in enumerate(_t_candidates):
                        _tr = st.columns([3, 1, 2, 2])
                        _tr[0].write(_tc["display"])
                        _tr[1].write(_tc["cmc"])
                        _tr[2].write(_tc["type"])
                        if _tr[3].button("選択", key=f"tut_{_ti}_{_tc['name']}"):
                            import random
                            _np = list(_pending)
                            _np.pop(0)
                            _nl = [c for c in _library if c is not _tc]
                            random.shuffle(_nl)
                            st.session_state._sim_hand = list(_hand) + [_tc]
                            st.session_state._sim_library = _nl
                            st.session_state._sim_pending_effects = _np
                            st.rerun()

            elif _eff0_type == "rummage":
                _r_disc = _eff0.get("discard", 1)
                _r_draw = _eff0.get("draw", 1)
                _r_done = _eff0.get("discarded", 0)
                st.warning(f"⚡ **Rummage**: あと{_r_disc - _r_done}枚捨ててから{_r_draw}枚ドローします")
                if not _hand:
                    st.warning("手札がありません。")
                    if st.button("スキップ", key="rummage_skip"):
                        _np = list(_pending); _np.pop(0)
                        st.session_state._sim_pending_effects = _np
                        st.rerun()
                else:
                    for _ri, _rc in enumerate(_hand):
                        _rrow = st.columns([3, 1, 2, 2])
                        _rrow[0].write(_rc["display"])
                        _rrow[1].write(_rc["cmc"])
                        _rrow[2].write(_rc["type"])
                        if _rrow[3].button("捨てる", key=f"rummage_{_ri}_{_rc['name']}"):
                            _np = list(_pending)
                            _new_disc_done = _r_done + 1
                            _new_hand = [c for j, c in enumerate(_hand) if j != _ri]
                            _new_gy = list(_graveyard) + [_rc]
                            if _new_disc_done >= _r_disc:
                                _np.pop(0)
                                _nl = list(_library)
                                _new_drawn_r = None
                                for _ in range(_r_draw):
                                    if _nl:
                                        _d = _nl.pop(0)
                                        _new_hand.append(_d)
                                        _new_drawn_r = _d
                                st.session_state._sim_library = _nl
                                st.session_state._sim_drawn = _new_drawn_r
                            else:
                                _np[0] = dict(_np[0])
                                _np[0]["discarded"] = _new_disc_done
                            st.session_state._sim_hand = _new_hand
                            st.session_state._sim_graveyard = _new_gy
                            st.session_state._sim_pending_effects = _np
                            st.rerun()

            elif _eff0_type == "graveyard_return":
                st.info("♻️ **墓地から 1 枚を手札に戻してください**")
                if not _graveyard:
                    st.warning("墓地にカードがありません。")
                    if st.button("スキップ", key="gy_ret_skip"):
                        _np = list(_pending)
                        _np.pop(0)
                        st.session_state._sim_pending_effects = _np
                        st.rerun()
                else:
                    for _gi, _gc in enumerate(_graveyard):
                        _gr = st.columns([3, 1, 2, 2])
                        _gr[0].write(_gc["display"])
                        _gr[1].write(_gc["cmc"])
                        _gr[2].write(_gc["type"])
                        if _gr[3].button("手札に戻す", key=f"gy_ret_{_gi}_{_gc['name']}"):
                            _np = list(_pending)
                            _np.pop(0)
                            _ng = [c for c in _graveyard if c is not _gc]
                            st.session_state._sim_hand = list(_hand) + [_gc]
                            st.session_state._sim_graveyard = _ng
                            st.session_state._sim_pending_effects = _np
                            st.rerun()

            elif _eff0_type == "bounce_land":
                st.warning("🔄 **バウンスランド**: コントロールしている土地を1枚手札に戻してください")
                _bl_choices = [(j, c) for j, c in enumerate(_battlefield) if c.get("type") == "Land"]
                if not _bl_choices:
                    if st.button("スキップ", key="bounce_skip"):
                        _np = list(_pending); _np.pop(0)
                        st.session_state._sim_pending_effects = _np
                        st.rerun()
                else:
                    for _bli, (_bl_idx, _bl_card) in enumerate(_bl_choices):
                        _blr = st.columns([4, 1, 2, 2])
                        _blr[0].write(_bl_card["display"])
                        _blr[1].write("⊤" if _tapped[_bl_idx] else "◇")
                        _blr[2].write("".join({"W":"⬜","U":"🔵","B":"⚫","R":"🔴","G":"🟢","C":"⬛"}.get(c,c) for c in _bl_card.get("produced_mana",[])))
                        if _blr[3].button("手札に戻す", key=f"bounce_{_bli}_{_bl_card['name']}"):
                            _np = list(_pending); _np.pop(0)
                            _new_bf = [c for j, c in enumerate(_battlefield) if j != _bl_idx]
                            _new_t  = [t for j, t in enumerate(_tapped) if j != _bl_idx]
                            st.session_state._sim_battlefield = _new_bf
                            st.session_state._sim_tapped = _new_t
                            st.session_state._sim_hand = list(_hand) + [_bl_card]
                            st.session_state._sim_pending_effects = _np
                            st.rerun()

            elif _eff0_type == "fetch":
                _f_filter = _eff0.get("filter", "")
                st.info(f"🔍 **フェッチ**: ライブラリから **{_f_filter or '土地'}** カードを選んでください（タップインで場に出ます）")
                # filter: "island or mountain" → ["island", "mountain"]
                _f_types = [w.strip() for w in _f_filter.split(" or ")] if _f_filter else []
                _f_candidates = [
                    c for c in _library
                    if c.get("type") == "Land" and (
                        not _f_types
                        or any(ft in c.get("type_line", "").lower() for ft in _f_types)
                    )
                ]
                if not _f_candidates:
                    st.warning("条件に合う土地がライブラリにありません。")
                    if st.button("スキップ", key="fetch_skip"):
                        _np = list(_pending)
                        _np.pop(0)
                        st.session_state._sim_pending_effects = _np
                        st.rerun()
                else:
                    for _fi, _fc in enumerate(_f_candidates):
                        _fr = st.columns([3, 2, 2, 2])
                        _fr[0].write(_fc["display"])
                        _fr[1].write(_fc.get("type_line", _fc["type"]))
                        _fr[2].write("".join({"W":"⬜","U":"🔵","B":"⚫","R":"🔴","G":"🟢","C":"⬛"}.get(c,c) for c in _fc.get("produced_mana",[])))
                        if _fr[3].button("選択", key=f"fetch_{_fi}_{_fc['name']}"):
                            import random
                            _np = list(_pending)
                            _np.pop(0)
                            _nl = [c for c in _library if c is not _fc]
                            random.shuffle(_nl)
                            # フェッチで場に出た土地のエントリー効果を確認
                            _fc_linfo = _classify_land(_fc.get("oracle_text", ""))
                            _fc_ltype = _fc_linfo["type"]
                            if _fc_ltype == "scry_land":
                                _sc_ex = [_nl.pop(0) for _ in range(_fc_linfo.get("scry", 1)) if _nl]
                                if _sc_ex:
                                    _np.insert(0, {"type": "scry", "cards": _sc_ex})
                            elif _fc_ltype == "surveil_land":
                                _sv_ex = [_nl.pop(0) for _ in range(_fc_linfo.get("surveil", 1)) if _nl]
                                if _sv_ex:
                                    _np.insert(0, {"type": "surveil", "cards": _sv_ex})
                            st.session_state._sim_battlefield = list(_battlefield) + [_fc]
                            st.session_state._sim_tapped = list(_tapped) + [True]
                            st.session_state._sim_library = _nl
                            st.session_state._sim_pending_effects = _np
                            st.rerun()

        if _alt_casting:
            _ac = _alt_cast_card
            st.info(f"🪄 **代替コストで唱える**: {_ac['display']}")
            _acc1, _acc2, _acc3 = st.columns([3, 3, 2])
            _acc1.write(f"印刷コスト: `{_ac['mana_cost'] or '(なし)'}`")
            _alt_cost_val = _acc2.text_input(
                "実際に払うコスト",
                value=_ac.get("mana_cost", ""),
                key="alt_cost_input",
                label_visibility="collapsed",
                placeholder="{U}{B}など",
            )
            _btn1, _btn2 = _acc3.columns(2)
            if _btn1.button("✨ 確定", key="alt_cast_confirm"):
                _alt_mana_pool = st.session_state.get("_sim_mana_pool", {})
                _alt_virtual_tapped = [
                    t or _is_summoning_sick(_battlefield[i], _turn)
                    for i, t in enumerate(_tapped)
                ]
                _alt_tap, _alt_pool_after = _compute_tapping(_battlefield, _alt_virtual_tapped, _alt_cost_val, _alt_mana_pool)
                if _alt_tap is None:
                    st.error("指定コストでも支払えません。土地が不足しています。")
                else:
                    _execute_cast(_ac, _alt_tap, _alt_pool_after)
            if _btn2.button("✖", key="alt_cast_cancel"):
                st.session_state._sim_alt_cast_card = None
                st.rerun()

        st.markdown(f"**手札（{len(_hand)}枚）**")
        hdr = st.columns([1, 2, 4, 2, 2])
        hdr[0].markdown("**CMC**")
        hdr[1].markdown("**マナ**")
        hdr[2].markdown("**カード名**")
        hdr[3].markdown("**タイプ**")
        hdr[4].markdown("**アクション**")

        display_hand = (
            [c for c in _hand if c is _drawn] + [c for c in _hand if c is not _drawn]
            if _drawn else list(_hand)
        )

        for i, card in enumerate(display_hand):
            is_drawn = (card is _drawn)
            row = st.columns([1, 2, 4, 2, 2])
            row[0].write(card["cmc"])
            row[1].write(_mana_emoji(card["mana_cost"]))
            row[2].write(f"★ {card['display']}" if is_drawn else card["display"])
            row[3].write(card["type"])

            if _pending:
                _eff = _pending[0]
                if _eff["type"] in ("discard", "return"):
                    # ── エフェクト処理中：捨てる / ライブラリへ戻す ──────────
                    _lbl = "捨てる" if _eff["type"] == "discard" else "↩ 戻す"
                    if row[4].button(_lbl, key=f"pend_{i}_{card['name']}"):
                        _new_hand = []
                        _done = False
                        for c in _hand:
                            if c is card and not _done:
                                _done = True
                                continue
                            _new_hand.append(c)
                        _new_pending = list(_pending)
                        _new_pending[0] = {**_eff, "count": _eff["count"] - 1}
                        if _new_pending[0]["count"] <= 0:
                            _new_pending.pop(0)
                        st.session_state._sim_hand = _new_hand
                        if _eff["type"] == "discard":
                            st.session_state._sim_graveyard = list(_graveyard) + [card]
                        else:
                            st.session_state._sim_library = [card] + list(_library)
                        st.session_state._sim_pending_effects = _new_pending
                        st.rerun()
                else:
                    row[4].write("(処理中)")

            elif _alt_casting:
                # ── 代替コスト入力中 ──────────────────────────────────────
                if card is _alt_cast_card:
                    row[4].write("↑ 入力中")
                else:
                    row[4].write("(処理中)")

            elif card["type"] == "Land":
                # ── 土地：プレイ（種別判定付き）─────────────────────────
                if _lands_played < 1:
                    _oracle = card.get("oracle_text", "")
                    _linfo  = _classify_land(_oracle)
                    _ltype  = _linfo["type"]
                    _other_lands = len([c for c in _battlefield if c.get("type") == "Land"])
                    _basic_lands = sum(1 for c in _battlefield if "Basic" in c.get("type_line", "") and c.get("type") == "Land")

                    if _ltype == "shock":
                        # ショックランド: 2ライフ払うかどうか選択
                        _sc1, _sc2 = row[4].columns(2)
                        if _sc1.button("🌍 -2💗", key=f"shock_free_{i}_{card['name']}", help="2ライフ払ってアンタップインする"):
                            _play_land(card, tapped=False)
                        if _sc2.button("🌍 ⊤", key=f"shock_tap_{i}_{card['name']}", help="ライフを払わずタップインする"):
                            _play_land(card, tapped=True)

                    elif _ltype == "reveal":
                        # リビールランド: 手札に該当カードがあれば選択肢を出す
                        _req = _linfo.get("requires", "")
                        _req_types = [r.strip().removeprefix("a ").removeprefix("an ") for r in re.split(r'\bor\b', _req)]
                        _has_in_hand = any(
                            any(rt.lower() in c.get("type_line", "").lower() for rt in _req_types if rt)
                            for c in _hand if c is not card
                        )
                        if _has_in_hand:
                            _rv1, _rv2 = row[4].columns(2)
                            if _rv1.button("🌍 リビール", key=f"reveal_free_{i}_{card['name']}", help=f"{_req} を公開してアンタップイン"):
                                _play_land(card, tapped=False)
                            if _rv2.button("🌍 ⊤", key=f"reveal_tap_{i}_{card['name']}", help="公開せずタップイン"):
                                _play_land(card, tapped=True)
                        else:
                            if row[4].button("🌍 ⊤", key=f"play_{i}_{card['name']}", help=f"手札に{_req}がないためタップイン"):
                                _play_land(card, tapped=True)

                    else:
                        # 自動判定系・フェッチ・バウンス・スクライ
                        if _ltype == "fetch":
                            _enters_tapped = None
                            _play_lbl = "🌍 フェッチ"
                            _play_help = "ライブラリから土地をサーチ"
                        elif _ltype == "bounce":
                            _enters_tapped = True
                            _play_lbl = "🌍 バウンス ⊤"
                            _play_help = "タップインし、土地1枚を手札に戻す"
                        elif _ltype == "scry_land":
                            _scry_n = _linfo.get("scry", 1)
                            _enters_tapped = True
                            _play_lbl = f"🌍 Scry{_scry_n} ⊤"
                            _play_help = f"タップインし、Scry {_scry_n} を行う"
                        elif _ltype == "surveil_land":
                            _surv_n = _linfo.get("surveil", 1)
                            _enters_tapped = True
                            _play_lbl = f"🌍 Surveil{_surv_n} ⊤"
                            _play_help = f"タップインし、Surveil {_surv_n} を行う"
                        elif _ltype == "fast":
                            _enters_tapped = _other_lands > 2
                            _play_lbl = "🌍 プレイ" + (" ⊤" if _enters_tapped else "")
                            _play_help = f"他の土地{_other_lands}枚 → {'3枚以上のためタップイン' if _enters_tapped else 'アンタップイン'}"
                        elif _ltype == "bfz":
                            _enters_tapped = _basic_lands < 2
                            _play_lbl = "🌍 プレイ" + (" ⊤" if _enters_tapped else "")
                            _play_help = f"基本地{_basic_lands}枚 → {'2枚未満のためタップイン' if _enters_tapped else 'アンタップイン'}"
                        elif _ltype == "check":
                            _req = _linfo.get("requires", "")
                            _req_types = [r.strip().removeprefix("a ").removeprefix("an ") for r in re.split(r'\bor\b|\band\b', _req)]
                            _has_req = any(
                                any(rt.lower() in c.get("type_line", "").lower() for rt in _req_types if rt)
                                for c in _battlefield if c.get("type") == "Land"
                            )
                            _enters_tapped = not _has_req
                            _play_lbl = "🌍 プレイ" + (" ⊤" if _enters_tapped else "")
                            _play_help = f"{_req} → {'なし、タップイン' if _enters_tapped else 'あり、アンタップイン'}"
                        elif _ltype == "tapland":
                            _enters_tapped = True
                            _play_lbl = "🌍 プレイ ⊤"
                            _play_help = "タップインで戦場に出る"
                        else:  # normal
                            _enters_tapped = False
                            _play_lbl = "🌍 プレイ"
                            _play_help = ""

                        if row[4].button(_play_lbl, key=f"play_{i}_{card['name']}", help=_play_help or None):
                            if _ltype == "fetch":
                                _play_fetchland(card)
                            elif _ltype == "bounce":
                                _play_land(card, tapped=True, extra_pending=[{"type": "bounce_land"}])
                            elif _ltype == "scry_land":
                                _nl = list(_library)
                                _sc_ex = [_nl.pop(0) for _ in range(_linfo.get("scry", 1)) if _nl]
                                _play_land(card, tapped=True,
                                           extra_pending=[{"type": "scry", "cards": _sc_ex}] if _sc_ex else None,
                                           new_library=_nl)
                            elif _ltype == "surveil_land":
                                _nl = list(_library)
                                _sv_ex = [_nl.pop(0) for _ in range(_linfo.get("surveil", 1)) if _nl]
                                _play_land(card, tapped=True,
                                           extra_pending=[{"type": "surveil", "cards": _sv_ex}] if _sv_ex else None,
                                           new_library=_nl)
                            else:
                                _play_land(card, tapped=_enters_tapped)
                else:
                    row[4].write("(プレイ済)")

            else:
                # ── スペル：唱える ───────────────────────────────────────
                _mana_pool = st.session_state.get("_sim_mana_pool", {})
                _virtual_tapped = [
                    t or _is_summoning_sick(_battlefield[i], _turn)
                    for i, t in enumerate(_tapped)
                ]
                _tap_indices, _pool_after = _compute_tapping(_battlefield, _virtual_tapped, card["mana_cost"], _mana_pool)
                _c_cast, _c_alt = row[4].columns(2)
                if _c_cast.button("✨ 唱える", key=f"cast_{i}_{card['name']}", disabled=(_tap_indices is None)):
                    _execute_cast(card, _tap_indices, _pool_after)
                if _c_alt.button("🪄 代替", key=f"altcast_{i}_{card['name']}"):
                    st.session_state._sim_alt_cast_card = card
                    st.rerun()

        st.divider()

        # ── ボタン行 ─────────────────────────────────────────────────────
        sb1, sb2, sb3 = st.columns(3)
        if sb1.button("🎲 マリガン", key="sim_mulligan"):
            import random
            all_cards = [c for c in list(_library) + list(_hand) + list(_battlefield) + list(_graveyard) if not c.get("is_token")]
            random.shuffle(all_cards)
            st.session_state._sim_hand = all_cards[:7]
            st.session_state._sim_library = all_cards[7:]
            st.session_state._sim_mulligans += 1
            st.session_state._sim_drawn = None
            st.session_state._sim_battlefield = []
            st.session_state._sim_graveyard = []
            st.session_state._sim_lands_played = 0
            st.session_state._sim_tapped = []
            st.session_state._sim_pending_effects = []
            st.session_state._sim_alt_cast_card = None
            st.session_state._sim_mana_pool = {}
            st.rerun()
        if sb2.button(f"➡ ターン{_turn + 1}へ（ドロー）", key="sim_draw"):
            if _library:
                _drawn_card = _library[0]
                st.session_state._sim_hand = list(_hand) + [_drawn_card]
                st.session_state._sim_library = _library[1:]
                st.session_state._sim_turn += 1
                st.session_state._sim_drawn = _drawn_card
                st.session_state._sim_lands_played = 0
                st.session_state._sim_alt_cast_card = None
                # アンタップ：全土地を未タップ状態に戻す
                st.session_state._sim_tapped = [False] * len(_battlefield)
                st.session_state._sim_mana_pool = {}
                st.rerun()
            else:
                st.error("ライブラリが空です。")
        if sb3.button("🔄 リセット", key="sim_reset"):
            st.session_state._sim_hand = []
            st.session_state._sim_library = []
            st.session_state._sim_turn = 0
            st.session_state._sim_mulligans = 0
            st.session_state._sim_drawn = None
            st.session_state._sim_battlefield = []
            st.session_state._sim_graveyard = []
            st.session_state._sim_lands_played = 0
            st.session_state._sim_tapped = []
            st.session_state._sim_pending_effects = []
            st.session_state._sim_alt_cast_card = None
            st.session_state._sim_mana_pool = {}
            st.rerun()
