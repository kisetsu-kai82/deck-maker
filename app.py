import csv
import io

import streamlit as st

from deck_builder.analysis import color_distribution, deck_stats, mana_curve


def primary_type(type_line: str) -> str:
    """Extract primary card type from type_line (e.g. 'Creature — Human' → 'Creature')."""
    main = type_line.split("—")[0].strip()
    for t in ("Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Land", "Planeswalker", "Battle"):
        if t in main:
            return t
    return main if main else "Unknown"


def type_distribution(deck) -> dict[str, int]:
    dist: dict[str, int] = {}
    for card in deck.list_cards():
        t = primary_type(card.type_line)
        dist[t] = dist.get(t, 0) + card.count
    return dict(sorted(dist.items(), key=lambda x: -x[1]))
from deck_builder.deck import Card, Deck
from deck_builder.scryfall import ScryfallClient
from deck_builder.storage import deck_exists, list_decks, load_deck, save_deck

st.set_page_config(page_title="MTG Deck Builder", layout="wide")

# --- Session state init ---
if "selected_deck" not in st.session_state:
    st.session_state.selected_deck = None
if "_candidates" not in st.session_state:
    st.session_state._candidates = []
if "_pending_count" not in st.session_state:
    st.session_state._pending_count = 4


def reload_deck_list():
    st.session_state._deck_names = list_decks()


if "_deck_names" not in st.session_state:
    st.session_state._deck_names = list_decks()

# --- Sidebar ---
with st.sidebar:
    st.title("MTG Deck Builder")

    deck_names = st.session_state._deck_names

    if deck_names:
        selected = st.radio(
            "デッキ一覧",
            deck_names,
            index=deck_names.index(st.session_state.selected_deck)
            if st.session_state.selected_deck in deck_names
            else 0,
        )
        st.session_state.selected_deck = selected
    else:
        st.info("デッキがありません。新規作成してください。")

    st.divider()

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
                st.session_state.selected_deck = new_name.lower().replace(" ", "_")
                reload_deck_list()
                st.rerun()

# --- Main area ---
selected_deck_name = st.session_state.selected_deck

if not selected_deck_name:
    st.info("サイドバーからデッキを選択するか、新規作成してください。")
    st.stop()

try:
    deck = load_deck(selected_deck_name)
except FileNotFoundError:
    st.error(f"デッキ '{selected_deck_name}' が見つかりません。")
    st.stop()

st.title(deck.name)
if deck.format:
    st.caption(f"[{deck.format}]")

tab_cards, tab_analyze = st.tabs(["Cards", "Analyze"])

# --- Cards tab ---
with tab_cards:
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        card_name_input = st.text_input("カード名", key="card_name_input")
    with col2:
        card_count_input = st.number_input("枚数", min_value=1, max_value=99, value=4, key="card_count_input")
    with col3:
        st.write("")
        st.write("")
        add_clicked = st.button("Scryfallから追加")

    if add_clicked:
        if not card_name_input.strip():
            st.error("カード名を入力してください。")
        else:
            st.session_state._candidates = []
            with st.spinner("取得中..."):
                try:
                    client = ScryfallClient()
                    # Step 1: exact match (cache or Scryfall exact)
                    try:
                        data = client.get_card_exact(card_name_input.strip())
                        card = Card(
                            name=data["name"],
                            mana_cost=data.get("mana_cost", ""),
                            cmc=float(data.get("cmc", 0)),
                            colors=data.get("colors", []),
                            type_line=data.get("type_line", ""),
                            count=int(card_count_input),
                            printed_name=data.get("printed_name", ""),
                        )
                        deck.add_card(card, int(card_count_input))
                        save_deck(deck)
                        st.success(f"{card.display_name()} x{int(card_count_input)} を追加しました。")
                        st.rerun()
                    except ValueError:
                        # Step 2: search for candidates
                        candidates = client.search_candidates(card_name_input.strip())
                        if not candidates:
                            st.error("カードが見つかりませんでした。")
                        elif len(candidates) == 1:
                            data = client.get_card(candidates[0]["en_name"])
                            card = Card(
                                name=data["name"],
                                mana_cost=data.get("mana_cost", ""),
                                cmc=float(data.get("cmc", 0)),
                                colors=data.get("colors", []),
                                type_line=data.get("type_line", ""),
                                count=int(card_count_input),
                                printed_name=data.get("printed_name", ""),
                            )
                            deck.add_card(card, int(card_count_input))
                            save_deck(deck)
                            st.success(f"{card.display_name()} x{int(card_count_input)} を追加しました。")
                            st.rerun()
                        else:
                            # Multiple candidates: show picker
                            st.session_state._candidates = candidates
                            st.session_state._pending_count = int(card_count_input)
                except Exception as e:
                    st.error(f"カードの取得に失敗しました: {e}")

    # Candidate picker
    if st.session_state._candidates:
        st.warning(f"{len(st.session_state._candidates)} 件の候補が見つかりました。追加するカードを選択してください:")
        hdr = st.columns([4, 3, 2, 1])
        hdr[0].markdown("**カード名**")
        hdr[1].markdown("**タイプ**")
        hdr[2].markdown("**マナコスト**")
        for cand in st.session_state._candidates:
            row = st.columns([4, 3, 2, 1])
            display = cand["en_name"]
            if cand.get("ja_name"):
                display = f"{cand['ja_name']}/{display}"
            row[0].write(display)
            row[1].write(cand.get("type_line", ""))
            row[2].write(cand.get("mana_cost", ""))
            if row[3].button("追加", key=f"cand_{cand['en_name']}"):
                try:
                    client = ScryfallClient()
                    data = client.get_card(cand["en_name"])
                    card = Card(
                        name=data["name"],
                        mana_cost=data.get("mana_cost", ""),
                        cmc=float(data.get("cmc", 0)),
                        colors=data.get("colors", []),
                        type_line=data.get("type_line", ""),
                        count=st.session_state._pending_count,
                        printed_name=data.get("printed_name", ""),
                    )
                    deck.add_card(card, st.session_state._pending_count)
                    save_deck(deck)
                    st.session_state._candidates = []
                    st.rerun()
                except Exception as e:
                    st.error(f"カードの取得に失敗しました: {e}")
        if st.button("キャンセル", key="cancel_candidates"):
            st.session_state._candidates = []
            st.rerun()

    # --- CSV import ---
    st.divider()
    uploaded = st.file_uploader("CSVからインポート (count,name)", type="csv", key="csv_uploader")
    if uploaded is not None:
        try:
            content = uploaded.read().decode("utf-8-sig")  # BOM対応
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
                        card = Card(
                            name=data["name"],
                            mana_cost=data.get("mana_cost", ""),
                            cmc=float(data.get("cmc", 0)),
                            colors=data.get("colors", []),
                            type_line=data.get("type_line", ""),
                            count=count,
                            printed_name=data.get("printed_name", ""),
                        )
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

    cards = deck.list_cards()
    if not cards:
        st.info("カードがありません。上のフォームから追加してください。")
    else:
        # Build display with delete buttons
        header_cols = st.columns([1, 3, 2, 2, 1, 1])
        header_cols[0].markdown("**枚数**")
        header_cols[1].markdown("**カード名**")
        header_cols[2].markdown("**タイプ**")
        header_cols[3].markdown("**マナコスト**")
        header_cols[4].markdown("**CMC**")
        header_cols[5].markdown("**削除**")

        for card in cards:
            row_cols = st.columns([1, 3, 2, 2, 1, 1])
            row_cols[0].write(card.count)
            row_cols[1].write(card.display_name())
            row_cols[2].write(primary_type(card.type_line))
            row_cols[3].write(card.mana_cost)
            row_cols[4].write(int(card.cmc))
            if row_cols[5].button("×", key=f"del_{card.name}"):
                deck.remove_card(card.name)
                save_deck(deck)
                st.rerun()

# --- Analyze tab ---
with tab_analyze:
    stats = deck_stats(deck)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Cards", stats["total"])
    m2.metric("Unique Cards", stats["unique"])
    m3.metric("Avg CMC", stats["avg_cmc"])

    st.subheader("マナカーブ")
    curve = mana_curve(deck)
    if curve:
        curve_data = {str(k): v for k, v in curve.items()}
        st.bar_chart(curve_data)
    else:
        st.info("データがありません。")

    st.subheader("色分布")
    color_names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
    dist = color_distribution(deck)
    if dist:
        dist_named = {color_names.get(k, k): v for k, v in dist.items()}
        st.bar_chart(dist_named)
    else:
        st.info("データがありません。")

    st.subheader("カードタイプ分布")
    type_dist = type_distribution(deck)
    if type_dist:
        st.bar_chart(type_dist)
    else:
        st.info("データがありません。")
