import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, date

# Google認証とスプレッドシート設定
creds_path = r"C:\Users\k_uemura\Desktop\zaikokanri\credentials.json"
SPREADSHEET_NAME = "zaikokanri"
gc = gspread.service_account(filename=creds_path)

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

def add_checkout_log(cart, destination, borrower, start_date, end_date):
    worksheet = gc.open(SPREADSHEET_NAME).worksheet('CheckoutLog')
    existing_data = worksheet.get_all_records()
    max_log_id = max([int(row['ログID']) for row in existing_data], default=0)
    for item_id, qty in cart.items():
        item = items_df[items_df['品物ID'] == item_id].iloc[0]
        max_log_id += 1
        new_row = [
            max_log_id,
            item_id,
            item['品物名'],
            qty,
            destination,
            borrower,
            start_date.strftime('%Y-%m-%d'),
            end_date.strftime('%Y-%m-%d'),
            'FALSE',
            0
        ]
        worksheet.append_row(new_row)
    st.success("持ち出しを登録しました！")
    st.session_state.cart = {}
    st.rerun()

def show_home():
    st.title("🏠 備品管理システム")
    keyword = st.text_input("🔍 在庫検索（品物名を入力）", key="home_search_input")
    if keyword:
        filtered = items_df[items_df['品物名'].str.contains(keyword, case=False, na=False)]
        grouped = filtered.groupby('品物名')['品物ID'].apply(list).reset_index()
        st.subheader(f"🔎 検索結果（{len(grouped)}件）")
        for _, row in grouped.iterrows():
            group_name = row['品物名']
            if st.button(f"{group_name}", key=f"home_search_btn_{group_name}"):
                st.session_state.selected_item = row['品物ID'][0]
                go_to("list_detail", prev_page="home")
                st.rerun()
    else:
        st.write("検索ワードを入力してください。")

    if st.button("📋 在庫一覧", key="home_zaiko_button"):
        go_to("list", prev_page="home")
        st.rerun()
    if st.button("🚚 持ち出し中確認", key="home_checkout_button"):
        go_to("checkout_status", prev_page="home")
        st.rerun()
    if st.button("🛒 カートを見る", key="home_cart_button"):
        go_to("cart", prev_page="home")
        st.rerun()

def show_cart():
    st.title("🛒 カート内の品物一覧")
    cart = st.session_state.get('cart', {})
    if not cart:
        st.write("カートには何も入っていません。")
    else:
        to_remove = []
        for item_id, qty in cart.items():
            item = items_df[items_df['品物ID'] == item_id]
            if not item.empty:
                item_name = item.iloc[0]['品物名']
                detail = item.iloc[0].get('詳細', '')
                max_qty = item.iloc[0]['残りの在庫数'] + qty
                new_qty = st.number_input(f"{item_name}（詳細: {detail}）", min_value=0, max_value=max_qty, value=qty, step=1, key=f"cart_qty_{item_id}")
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

def update_checkout_log_after_return(return_items):
    worksheet = gc.open(SPREADSHEET_NAME).worksheet('CheckoutLog')
    for log_id, qty in return_items.items():
        checkout_log = checkout_df[checkout_df['ログID'] == log_id]
        if not checkout_log.empty:
            idx = checkout_log.index[0]
            checkout_df.at[idx, '返却済み（TRUE/FALSE）'] = 'TRUE'
            checkout_df.at[idx, '返却数量'] = qty
            worksheet.update_cell(idx + 2, checkout_log.columns.get_loc('返却済み（TRUE/FALSE）') + 1, 'TRUE')
            worksheet.update_cell(idx + 2, checkout_log.columns.get_loc('返却数量') + 1, qty)
    st.success("返却処理を完了しました！")
    st.rerun()

def show_checkout_status():
    st.title("🚚 持ち出し中のアイテム")
    active_checkout = checkout_df[checkout_df['返却済み（TRUE/FALSE）'].astype(str).str.upper() != 'TRUE']
    if active_checkout.empty:
        st.write("現在、持ち出し中のアイテムはありません。")
    else:
        groups = active_checkout.groupby(['持ち出し先', '持ち出し者'])
        for (destination, person), group in groups:
            btn_label = f"持ち出し先: {destination} / 持ち出し者: {person}"
            if st.button(btn_label, key=f"checkout_btn_{destination}_{person}"):
                go_to("return_detail", destination=destination, person=person)
                st.rerun()
            st.write(f"開始日: {group['持ち出し開始日'].min()} / 終了日: {group['持ち出し終了日'].max()}")
            st.markdown("---")
    if st.button("🔙 ホームに戻る", key="checkout_back_home_button"):
        go_to("home")
        st.rerun()

def show_return_detail():
    destination = st.session_state.page_params.get('destination')
    person = st.session_state.page_params.get('person')
    st.title(f"↩️ 返却処理（{destination} / {person}）")
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
            default_qty = int(row['持ち出し数'])
            checked = st.checkbox(f"{row['品物名']} | 数量: {default_qty}", key=f"return_checkbox_{row['ログID']}")
            if checked:
                qty = st.number_input(f"返却数量（{row['品物名']}）", min_value=1, max_value=default_qty, value=default_qty, key=f"return_qty_{row['ログID']}")
                return_items[row['ログID']] = qty
        if st.button("返却する", key="return_confirm_button") and return_items:
            update_checkout_log_after_return(return_items)
    if st.button("🔙 ホームに戻る", key="return_back_home_button"):
        go_to("home")
        st.rerun()

def show_list():
    st.title("📋 在庫一覧")
    grouped = items_df.groupby('品物名')['品物ID'].apply(list).reset_index()
    for _, row in grouped.iterrows():
        group_name = row['品物名']
        if st.button(group_name, key=f"list_btn_{group_name}"):
            st.session_state.selected_item = row['品物ID'][0]
            go_to("list_detail", prev_page="list")
            st.rerun()
    if st.button("🔙 ホームに戻る", key="list_back_home_button"):
        go_to("home")
        st.rerun()

def show_list_detail():
    st.title("📦 詳細ページ")
    if 'expanded_items' not in st.session_state:
        st.session_state.expanded_items = set()
    selected_item_id = st.session_state.get('selected_item')
    if selected_item_id is None:
        st.write("品物が選択されていません。")
        if st.button("🔙 ホームに戻る", key="list_detail_back_home_button"):
            go_to("home")
            st.rerun()
        return
    item_row = items_df[items_df['品物ID'] == selected_item_id]
    if item_row.empty:
        st.write("品物が見つかりません。")
        if st.button("🔙 ホームに戻る", key="list_detail_back_home_button_2"):
            go_to("home")
            st.rerun()
        return

    prev_page = st.session_state.page_params.get('prev_page', 'home')

    group_name = item_row.iloc[0]['品物名']
    group_items = items_df[items_df['品物名'] == group_name]
    for _, item in group_items.iterrows():
        detail_info = item.get('詳細', str(item['品物ID']))
        item_key = f"item_{item['品物ID']}"
        btn_label = f"【{detail_info}】 元の在庫数: {item['元の在庫数']} / 持ち出し中: {item['持ち出し中の在庫数']} / 残り: {item['残りの在庫数']}"
        if st.button(btn_label, key=f"list_detail_btn_{item_key}"):
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
                qty = st.number_input(f"数量を選択 ({detail_info})", min_value=1, max_value=max_qty, key=f"list_detail_qty_{item['品物ID']}")
                if st.button(f"カートに入れる ({detail_info})", key=f"add_cart_{item['品物ID']}"):
                    cart = st.session_state.get('cart', {})
                    cart[item['品物ID']] = cart.get(item['品物ID'], 0) + qty
                    st.session_state.cart = cart
                    st.success(f"{detail_info} をカートに {qty} 個追加しました。")
        st.markdown("---")

    if st.button("🔙 前のページに戻る", key="list_detail_back_button"):
        go_to(prev_page)
        st.rerun()

    if st.button("🛒 カートを見る", key="list_detail_cart_button"):
        go_to("cart")
        st.rerun()

# 初期化
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

items_df, checkout_df, list_df = load_sheet_data()
items_df = calculate_remaining_stock(items_df, checkout_df)

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

