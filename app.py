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


def _compute_tapping(battlefield: list, tapped: list, cost_str: str):
    """スペルを唱えるためにタップする土地インデックスのリストを返す。
    払えない場合は None を返す。

    各土地は produced_mana の中から1色を選んで出力できる（1色=1マナ）。
    最も選択肢の少ない色要求を優先して割り当てる（最制約優先の貪欲法）。
    """
    cost = _parse_mana_cost(cost_str)
    if not cost:
        return []

    colored = {c: v for c, v in cost.items() if c != "generic"}
    generic = cost.get("generic", 0)
    avail_idx = [i for i, t in enumerate(tapped) if not t]

    # 色要求をリストに展開し、満たせる土地が少ない色から優先処理
    reqs = []
    for color, cnt in colored.items():
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
            return None  # この色要求を満たせる土地がない
        result_tapped.append(matched)
        remaining_avail.remove(matched)

    # generic 分の土地が残っているか確認してタップ
    if len(remaining_avail) < generic:
        return None
    result_tapped.extend(remaining_avail[:generic])
    return result_tapped


def _detect_hand_effects(oracle_text: str) -> list:
    """oracle_text から draw/discard/return 効果を検出する。
    may discard は任意なのでスキップ。draw は即時、discard/return はキュー。
    """
    if not oracle_text:
        return []
    effects = []
    text = oracle_text.lower()
    _num_pat = r'(a|an|\d+|two|three|four|five|six|seven)'

    def _n(s: str) -> int:
        return _NUM_WORDS.get(s, int(s) if s.isdigit() else 0)

    for m in re.finditer(rf'draw {_num_pat} cards?', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "draw", "count": n})

    for m in re.finditer(rf'discard {_num_pat} cards?', text):
        start = max(0, m.start() - 4)
        if "may" not in text[start:m.start()]:
            n = _n(m.group(1))
            if n > 0:
                effects.append({"type": "discard", "count": n})

    for m in re.finditer(rf'put {_num_pat} cards? from your hand on top of your library', text):
        n = _n(m.group(1))
        if n > 0:
            effects.append({"type": "return", "count": n})

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
                        # 完全一致で見つからなかった → 候補検索
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

    # テーブルの列幅比率 [枚数, カード名, タイプ, マナコスト, CMC, 移動, 削除]
    _CARD_COLS = [1, 3, 2, 2, 1, 1, 1]

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

    def _render_card_rows(container, cards, move_btn_label, move_fn, del_fn, key_prefix):
        """カード一覧の各行を描画する。

        container     : 描画先の Streamlit コンテナ（列など）
        cards         : 表示するカードのリスト
        move_btn_label: 移動ボタンのラベル（"↓" or "↑"）
        move_fn       : 移動ボタンを押したときに呼ぶ関数（deck.move_to_sideboard など）
        del_fn        : 削除ボタンを押したときに呼ぶ関数（deck.remove_card など）
        key_prefix    : 各ボタンのキーの接頭辞（同名ボタンの衝突を避ける）
        """
        if not cards:
            container.info("カードがありません。")
            return
        _card_table_header(container)
        for card in cards:
            rc = container.columns(_CARD_COLS)
            rc[0].write(card.count)
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

    def _render_section(cards, title, clear_key, clear_fn, move_btn_label, move_fn, del_fn, key_prefix):
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
            _render_card_rows(left, spells, move_btn_label, move_fn, del_fn, key_prefix + "_sp")
        with right:
            st.caption(f"土地（{sum(c.count for c in lands)}枚）")
            _render_card_rows(right, lands, move_btn_label, move_fn, del_fn, key_prefix + "_ld")

    # メインデッキとサイドボードを描画する
    main_cards = deck.list_cards()
    sb_cards   = deck.list_sideboard()

    _render_section(
        main_cards,
        f"#### メインデッキ（{deck.total_cards()}枚）",
        "clear_main", deck.clear_cards,
        "↓",           # メインデッキのカードはサイドへ移動（↓）
        deck.move_to_sideboard, deck.remove_card,
        "main",
    )

    _render_section(
        sb_cards,
        f"#### サイドボード（{deck.total_sideboard()}枚）",
        "clear_sb", deck.clear_sideboard,
        "↑",          # サイドボードのカードはメインへ移動（↑）
        deck.move_to_main, deck.remove_sideboard_card,
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

        # _tapped リストが battlefield と長さが合わない場合（古い state との互換）は補正
        if len(_tapped) != len(_battlefield):
            _tapped = [False] * len(_battlefield)
            st.session_state._sim_tapped = _tapped

        _untapped_count = sum(1 for t in _tapped if not t)

        # ── メトリクス行（5列）──────────────────────────────────────────────
        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("ターン", _turn)
        sm2.metric("手札", len(_hand))
        sm3.metric("ライブラリ", len(_library))
        sm4.metric("未タップ土地", _untapped_count)
        sm5.metric("マリガン", _mulligans)

        # ── 戦場（土地がある場合のみ）─────────────────────────────────────
        if _battlefield:
            _em_map = {"W": "⬜", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢", "C": "⬛"}
            _land_parts = []
            for _li, _ld in enumerate(_battlefield):
                _colors_str = "".join(_em_map.get(c, c) for c in _ld.get("produced_mana", []))
                _tap_mark = "⊤" if _tapped[_li] else "◇"
                _land_parts.append(f"{_tap_mark}{_ld['display']}({_colors_str or '?'})")
            st.markdown("🏔 **戦場**: " + "  ".join(_land_parts))

        st.divider()

        # ── 手札表示 ─────────────────────────────────────────────────────
        if _pending:
            _eff0 = _pending[0]
            _verb = "捨てて" if _eff0["type"] == "discard" else "ライブラリへ戻して"
            st.warning(f"⚠️ {_eff0['count']}枚{_verb}ください")

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
                # ── エフェクト処理中：捨てる / ライブラリへ戻す ──────────
                _eff = _pending[0]
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

            elif card["type"] == "Land":
                # ── 土地：プレイ（色選択なし・戦場にそのまま追加）────────
                if _lands_played < 1:
                    if row[4].button("🌍 プレイ", key=f"play_{i}_{card['name']}"):
                        _new_hand = []
                        _done = False
                        for c in _hand:
                            if c is card and not _done:
                                _done = True
                                continue
                            _new_hand.append(c)
                        st.session_state._sim_hand = _new_hand
                        st.session_state._sim_battlefield = list(_battlefield) + [card]
                        st.session_state._sim_tapped = list(_tapped) + [False]
                        st.session_state._sim_lands_played = _lands_played + 1
                        if card is _drawn:
                            st.session_state._sim_drawn = None
                        st.rerun()
                else:
                    row[4].write("(プレイ済)")

            else:
                # ── スペル：唱える ───────────────────────────────────────
                # 未タップ土地の組み合わせでコストを払えるか判定
                _tap_indices = _compute_tapping(_battlefield, _tapped, card["mana_cost"])
                if _tap_indices is not None:
                    if row[4].button("✨ 唱える", key=f"cast_{i}_{card['name']}"):
                        # 使用した土地をタップ済みにマーク
                        _new_tapped = list(_tapped)
                        for _ti in _tap_indices:
                            _new_tapped[_ti] = True

                        # 手札から除去・墓地へ
                        _new_hand = []
                        _done = False
                        for c in _hand:
                            if c is card and not _done:
                                _done = True
                                continue
                            _new_hand.append(c)
                        _new_graveyard = list(_graveyard) + [card]

                        # エフェクト検出・ドロー即時適用
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

                        # discard / return をキューへ
                        _new_pending = list(_pending)
                        for _e in _effs:
                            if _e["type"] in ("discard", "return"):
                                _new_pending.append(_e)

                        st.session_state._sim_hand = _new_hand
                        st.session_state._sim_library = _new_library
                        st.session_state._sim_graveyard = _new_graveyard
                        st.session_state._sim_tapped = _new_tapped
                        st.session_state._sim_pending_effects = _new_pending
                        st.session_state._sim_drawn = _new_drawn
                        st.rerun()
                else:
                    row[4].write("(マナ不足)")

        st.divider()

        # ── ボタン行 ─────────────────────────────────────────────────────
        sb1, sb2, sb3 = st.columns(3)
        if sb1.button("🎲 マリガン", key="sim_mulligan"):
            import random
            all_cards = list(_library) + list(_hand) + list(_battlefield) + list(_graveyard)
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
            st.rerun()
        if sb2.button(f"➡ ターン{_turn + 1}へ（ドロー）", key="sim_draw"):
            if _library:
                _drawn_card = _library[0]
                st.session_state._sim_hand = list(_hand) + [_drawn_card]
                st.session_state._sim_library = _library[1:]
                st.session_state._sim_turn += 1
                st.session_state._sim_drawn = _drawn_card
                st.session_state._sim_lands_played = 0
                # アンタップ：全土地を未タップ状態に戻す
                st.session_state._sim_tapped = [False] * len(_battlefield)
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
            st.rerun()
