import streamlit as st
import pandas as pd
import gspread
import os
import json
import unicodedata
import pykakasi
from datetime import datetime, date
from google.oauth2.service_account import Credentials

# --- ふりがな変換セットアップ ---
kakasi = pykakasi.kakasi()
kakasi.setMode("J", "H")
kakasi.setMode("K", "H")
kakasi.setMode("H", "H")
converter = kakasi.getConverter()

def get_yomi(text):
    return converter.do(text)

# --- 認証処理（Cloud or ローカル自動判定） ---
creds_json = os.getenv('GOOGLE_CREDENTIALS')

if creds_json:
    creds_info = json.loads(creds_json)
else:
    local_path = "C:/Users/k_uemura/Desktop/zaikokanri/toumei/credentials.json"
    if os.path.exists(local_path):
        with open(local_path, "r", encoding="utf-8") as f:
            creds_info = json.load(f)
    else:
        st.stop()

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
gc = gspread.authorize(creds)
SPREADSHEET_NAME = "zaikokanri"

@st.cache_data(ttl=20)
def load_sheet_data():
    spreadsheet = gc.open(SPREADSHEET_NAME)
    items_df = pd.DataFrame(spreadsheet.worksheet("Items").get_all_records())
    checkout_df = pd.DataFrame(spreadsheet.worksheet("CheckoutLog").get_all_records())
    list_df = pd.DataFrame(spreadsheet.worksheet("List").get_all_records())
    items_df = items_df[items_df['品物名'].notna() & (items_df['品物名'] != '')]
    return items_df, checkout_df, list_df

def calculate_remaining_stock(items, checkout):
    items['品物ID'] = items['品物ID'].astype(str)
    checkout['品物ID'] = checkout['品物ID'].astype(str)
    items['元の在庫数'] = pd.to_numeric(items['元の在庫数'], errors='coerce').fillna(0).astype(int)
    checkout['持ち出し数'] = pd.to_numeric(checkout['持ち出し数'], errors='coerce').fillna(0).astype(int)
    not_returned = checkout[checkout['返却済み（TRUE/FALSE）'].astype(str).str.upper() != 'TRUE']
    merged = not_returned.merge(items[['品物ID', '品物名']], on='品物ID', how='left')
    checked_out = merged.groupby('品物ID')['持ち出し数'].sum().reset_index()
    checked_out.rename(columns={'持ち出し数': '持ち出し中の在庫数'}, inplace=True)
    items = items.merge(checked_out, on='品物ID', how='left')
    items['持ち出し中の在庫数'] = items['持ち出し中の在庫数'].fillna(0).astype(int)
    items['残りの在庫数'] = items['元の在庫数'] - items['持ち出し中の在庫数']
    return items

def go_to(page, **kwargs):
    st.session_state.page = page
    st.session_state.page_params = kwargs

def show_home():
    st.title("\U0001F3E0 備品管理システム")
    keyword = st.text_input("\U0001F50D 在庫検索（品物名を入力）")
    if keyword:
        filtered = items_df[items_df['品物名'].str.contains(keyword, case=False, na=False)]
        grouped = filtered.groupby('品物名')['品物ID'].apply(list).reset_index()
        st.subheader(f"\U0001F50E 検索結果（{len(grouped)}件）")
        for _, row in grouped.iterrows():
            group_name = row['品物名']
            if st.button(f"{group_name}", key=f"search_btn_{group_name}"):
                st.session_state.selected_item = row['品物ID'][0]
                go_to("list_detail")
                st.rerun()
    else:
        st.write("検索ワードを入力してください。")
    if st.button("\U0001F4CB 在庫一覧"):
        go_to("list")
        st.rerun()
    if st.button("\U0001F69A 持ち出し中確認"):
        go_to("checkout_status")
        st.rerun()
    if st.button("\U0001F6D2 カートを見る"):
        go_to("cart")
        st.rerun()

# --- 追加：持ち出しログ登録用関数 ---
def add_checkout_log(cart, destination, borrower, start_date, end_date):
    spreadsheet = gc.open(SPREADSHEET_NAME)
    log_ws = spreadsheet.worksheet("CheckoutLog")

    existing = log_ws.get_all_records()
    next_id = len(existing) + 1

    new_rows = []
    for item_id, qty in cart.items():
        item_row = items_df[items_df['品物ID'] == item_id].iloc[0]
        item_name = item_row['品物名']
        new_rows.append([
            next_id, item_id, item_name, qty, destination, borrower,
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            "FALSE"
        ])
        next_id += 1

    log_ws.append_rows(new_rows)
    st.session_state.cart = {}  # カートを空にする
    st.success("持ち出し処理が完了しました。")
    st.rerun()

# --- 修正済 show_cart 関数 ---
def show_cart():
    st.title("🛒 カート内の品物一覧")
    cart = st.session_state.get('cart', {})

    if not cart:
        st.write("カートには何も入っていません。")
    else:
        to_remove = []

        # 品物ごとの表示と数量変更
        for item_id, qty in cart.items():
            item = items_df[items_df['品物ID'] == item_id]
            if not item.empty:
                item_name = item.iloc[0]['品物名']
                detail = item.iloc[0].get('詳細', '')
                max_qty = item.iloc[0]['残りの在庫数'] + qty

                new_qty = st.number_input(
                    f"{item_name}（詳細: {detail}）",
                    min_value=0, max_value=max_qty, value=qty, step=1,
                    key=f"cart_qty_{item_id}"
                )

                if new_qty != qty:
                    if new_qty == 0:
                        to_remove.append(item_id)
                    else:
                        cart[item_id] = new_qty
                    st.session_state.cart = cart
                    st.rerun()
            else:
                st.write(f"品物ID {item_id} は在庫リストに存在しません。")

        for rem_id in to_remove:
            cart.pop(rem_id, None)

        st.session_state.cart = cart

        # 持ち出し情報入力セクション
        st.markdown("### 🚚 持ち出し情報を入力")

        destination_list = list_df['持ち出し先'].dropna().unique().tolist()
        borrower_list = list_df['持ち出し者'].dropna().unique().tolist()

        destination = st.selectbox("持ち出し先を選択", destination_list, key="cart_destination_select")
        borrower = st.selectbox("持ち出し者を選択", borrower_list, key="cart_borrower_select")
        start_date = st.date_input("持ち出し開始日", date.today(), key="cart_start_date")
        end_date = st.date_input("持ち出し終了日", date.today(), key="cart_end_date")

        if st.button("✅ 持ち出しを確定", key="cart_confirm_button"):
            add_checkout_log(cart, destination, borrower, start_date, end_date)

    if st.button("🔙 ホームに戻る", key="cart_back_home_button"):
        go_to("home")
        st.rerun()


def show_list():
    st.title("\U0001F4CB 在庫一覧")

    grouped = items_df.groupby('品物名')['品物ID'].apply(list).reset_index()
    grouped['読み'] = grouped['品物名'].apply(get_yomi)
    grouped = grouped.sort_values('読み').reset_index(drop=True)  # ← index を 0 始まりにリセット

    for i in range(0, len(grouped), 4):
        cols = st.columns(4)
        for j in range(4):
            if i + j < len(grouped):
                row = grouped.iloc[i + j]
                with cols[j]:
                    if st.button(row['品物名'], key=f"list_btn_{row['品物名']}"):
                        st.session_state.selected_item = row['品物ID'][0]
                        go_to("list_detail")
                        st.rerun()

    if st.button("\U0001F519 ホームに戻る"):
        go_to("home")
        st.rerun()


def show_list_detail():
    st.title("\U0001F4E6 詳細ページ")
    if 'expanded_items' not in st.session_state:
        st.session_state.expanded_items = set()
    selected_item_id = st.session_state.get('selected_item')
    if selected_item_id is None:
        st.write("品物が選択されていません。")
        if st.button("\U0001F519 ホームに戻る"):
            go_to("home")
            st.rerun()
        return
    item_row = items_df[items_df['品物ID'] == selected_item_id]
    if item_row.empty:
        st.write("品物が見つかりません。")
        if st.button("\U0001F519 ホームに戻る"):
            go_to("home")
            st.rerun()
        return
    group_name = item_row.iloc[0]['品物名']
    group_items = items_df[items_df['品物名'] == group_name]
    for _, item in group_items.iterrows():
        detail_info = item.get('詳細', str(item['品物ID']))
        item_key = f"item_{item['品物ID']}"
        btn_label = f"【{detail_info}】 元の在庫数: {item['元の在庫数']} / 持ち出し中: {item['持ち出し中の在庫数']} / 残り: {item['残りの在庫数']}"
        if st.button(btn_label, key=f"btn_{item_key}"):
            if item['品物ID'] in st.session_state.expanded_items:
                st.session_state.expanded_items.remove(item['品物ID'])
            else:
                st.session_state.expanded_items.add(item['品物ID'])
            st.rerun()
        if item['品物ID'] in st.session_state.expanded_items:
            max_qty = item['残りの在庫数']
            if max_qty <= 0:
                st.write("在庫なし")
            else:
                qty = st.number_input(f"数量を選択 ({detail_info})", min_value=1, max_value=max_qty, key=f"qty_{item['品物ID']}")
                if st.button(f"カートに入れる ({detail_info})", key=f"add_cart_{item['品物ID']}"):
                    cart = st.session_state.get('cart', {})
                    cart[item['品物ID']] = cart.get(item['品物ID'], 0) + qty
                    st.session_state.cart = cart
                    st.success(f"{detail_info} をカートに {qty} 個追加しました。")
        st.markdown("---")
    if st.button("\U0001F6D2 カートを見る"):
        go_to("cart")
        st.rerun()
    if st.button("\U0001F519 ホームに戻る"):
        go_to("home")
        st.rerun()

def show_checkout_status():
    st.title("\U0001F69A 持ち出し中のアイテム")
    active_checkout = checkout_df[checkout_df['返却済み（TRUE/FALSE）'].astype(str).str.upper() != 'TRUE']
    if active_checkout.empty:
        st.write("現在、持ち出し中のアイテムはありません。")
    else:
        groups = active_checkout.groupby(['持ち出し先', '持ち出し者'])
        for (destination, person), group in groups:
            btn_label = f"持ち出し先: {destination} / 持ち出し者: {person}"
            if st.button(btn_label, key=f"btn_{destination}_{person}"):
                go_to("return_detail", destination=destination, person=person)
                st.rerun()
            st.write(f"開始日: {group['持ち出し開始日'].min()} / 終了日: {group['持ち出し終了日'].max()}")
            st.markdown("---")
    if st.button("\U0001F519 ホームに戻る"):
        go_to("home")
        st.rerun()

def show_return_detail():
    destination = st.session_state.page_params.get('destination')
    person = st.session_state.page_params.get('person')
    st.title(f"\u21a9\ufe0f 返却処理（{destination} / {person}）")

    target = checkout_df[
        (checkout_df['持ち出し先'] == destination) &
        (checkout_df['持ち出し者'] == person) &
        (checkout_df['返却済み（TRUE/FALSE）'].astype(str).str.upper() != 'TRUE')
    ]

    if target.empty:
        st.write("返却待ちのアイテムはありません。")
    else:
        return_items = {}

        for _, row in target.iterrows():
            log_id = row['ログID']
            item_name = row['品物名']
            item_info = items_df[items_df['品物ID'] == row['品物ID']]
            detail = item_info.iloc[0]['詳細'] if not item_info.empty else ''
            default_qty = int(row['持ち出し数'])

            # --- 返却チェック ---
            checked = st.checkbox(f"{item_name} | {detail} | 数量: {default_qty}", key=f"return_checkbox_{log_id}")
            if checked:
                qty = st.number_input(
                    f"返却数量（{item_name}）", min_value=0, max_value=default_qty, value=default_qty,
                    key=f"qty_{log_id}"
                )

                # --- 破損チェック ---
                damaged = st.checkbox("破損したものがあるか", key=f"damaged_checkbox_{log_id}")
                damaged_qty = 0
                if damaged:
                    damaged_qty = st.number_input(
                        f"破損・滅失数量（{item_name}）", min_value=0, max_value=default_qty - qty,
                        value=0, key=f"damaged_qty_{log_id}"
                    )

                # --- 登録用データ構築 ---
                return_items[log_id] = {
                    "返却数量": qty,
                    "破損数量": damaged_qty,
                    "品物ID": row['品物ID']
                }

        if st.button("✅ 選択したアイテムを返却") and return_items:
            update_checkout_log_after_return(return_items)
            return

        if st.button("↩️ 全て選択して一括返却", key="return_all_button"):
            for _, row in target.iterrows():
                return_items[row['ログID']] = {
                    "返却数量": int(row['持ち出し数']),
                    "破損数量": 0,
                    "品物ID": row['品物ID']
                }
            update_checkout_log_after_return(return_items)
            return

    if st.button("\U0001F519 ホームに戻る"):
        go_to("home")
        st.rerun()



def update_checkout_log_after_return(return_items):
    spreadsheet = gc.open(SPREADSHEET_NAME)
    items_ws = spreadsheet.worksheet("Items")
    checkout_ws = spreadsheet.worksheet("CheckoutLog")

    for log_id, data in return_items.items():
        qty = data["返却数量"]
        damaged_qty = data["破損数量"]
        item_id = data["品物ID"]

        # --- CheckoutLogの更新 ---
        checkout_log = checkout_df[checkout_df['ログID'] == log_id]
        if not checkout_log.empty:
            idx = checkout_log.index[0]
            checkout_df.at[idx, '返却済み（TRUE/FALSE）'] = 'TRUE'
            checkout_df.at[idx, '返却数量'] = qty
            checkout_ws.update_cell(idx + 2, checkout_log.columns.get_loc('返却済み（TRUE/FALSE）') + 1, 'TRUE')
            checkout_ws.update_cell(idx + 2, checkout_log.columns.get_loc('返却数量') + 1, qty)

        # --- Itemsの元の在庫数を減らす（破損分）---
        item_row = items_df[items_df['品物ID'] == item_id]
        if not item_row.empty and damaged_qty > 0:
            item_idx = item_row.index[0]
            current_stock = int(items_df.at[item_idx, '元の在庫数'])
            new_stock = max(0, current_stock - damaged_qty)
            items_df.at[item_idx, '元の在庫数'] = new_stock
            items_ws.update_cell(item_idx + 2, items_df.columns.get_loc('元の在庫数') + 1, new_stock)

    st.success("返却処理を完了しました！")
    go_to("home")
    st.rerun()



# --- セッション初期化 ---
if 'page' not in st.session_state:
    st.session_state.page = 'home'
if 'selected_item' not in st.session_state:
    st.session_state.selected_item = None
if 'cart' not in st.session_state:
    st.session_state.cart = {}
if 'expanded_items' not in st.session_state:
    st.session_state.expanded_items = set()
if 'page_params' not in st.session_state:
    st.session_state.page_params = {}

# --- データ読込・在庫再計算 ---
items_df, checkout_df, list_df = load_sheet_data()
items_df = calculate_remaining_stock(items_df, checkout_df)

# --- ページルーティング ---
page = st.session_state.page
if page == 'home':
    show_home()
elif page == 'list':
    show_list()
elif page == 'list_detail':
    show_list_detail()
elif page == 'checkout_status':
    show_checkout_status()
elif page == 'cart':
    show_cart()
elif page == 'return_detail':
    show_return_detail()
else:
    st.error("無効なページ指定です。")