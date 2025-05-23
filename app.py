import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path
import datetime
import time  # st.spinner のデモ用
import re  # load_price_data で使用

# scraper.py から関数をインポート
try:
    from scraper import (
        scrape_prices_for_keyword,
        save_daily_stats,
        DATA_DIR,
        BRAND_FILE,
    )
except ImportError as e:
    st.error(f"scraper.pyのインポートに失敗しました: {e}")
    st.stop()


# === 定数・設定 ===
APP_TITLE = "メルカリ価格動向トラッカー"
DEFAULT_MOVING_AVERAGE_SHORT = 5
DEFAULT_MOVING_AVERAGE_LONG = 20
EXPECTED_COLUMNS = [
    "date",
    "keyword",
    "count",
    "average_price",
    "min_price",
    "max_price",
]  # CSVの期待列


# === ヘルパー関数 ===
@st.cache_data(ttl=3600)  # brands.jsonの内容を1時間キャッシュする
def load_brands_cached():
    """brands.jsonからブランド情報を読み込む (キャッシュ対応)"""
    if not BRAND_FILE.exists():
        st.warning(f"{BRAND_FILE} が見つかりません。サンプルを作成します。")
        default_brands_data = {
            "ストリート": ["Supreme", "Stussy", "A BATHING APE"],
            "モード系": ["Yohji Yamamoto", "COMME des GARCONS", "ISSEY MIYAKE"],
            "アウトドア": ["THE NORTH FACE", "Patagonia", "Arc'teryx"],
            "スニーカー": ["NIKE Air Jordan", "NIKE Dunk", "adidas Yeezy Boost"],
            "未分類": [],  # 新しいブランドを最初に追加しやすいように
        }
        try:
            with open(BRAND_FILE, "w", encoding="utf-8") as f:
                json.dump(default_brands_data, f, ensure_ascii=False, indent=2)
            st.info(f"デフォルトの {BRAND_FILE} を作成しました。")
            return default_brands_data
        except Exception as e:
            st.error(f"デフォルトの {BRAND_FILE} の作成に失敗しました: {e}")
            return {"未分類": []}  # エラー時は最低限のカテゴリを返す

    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        st.error(f"{BRAND_FILE} のJSON形式が正しくありません: {e}")
        st.info(
            "ファイル内容を確認するか、ファイルを一度削除して再実行するとデフォルトが作成されます。"
        )
        return {"未分類": []}
    except Exception as e:
        st.error(f"{BRAND_FILE} の読み込みに失敗しました: {e}")
        return {"未分類": []}


def save_brands_to_json(brands_data):
    """ブランドデータをbrands.jsonに保存する"""
    try:
        with open(BRAND_FILE, "w", encoding="utf-8") as f:
            json.dump(brands_data, f, ensure_ascii=False, indent=2)
        # キャッシュをクリアして次回読み込み時に最新版が使われるようにする
        load_brands_cached.clear()
        return True
    except Exception as e:
        st.error(f"brands.jsonへの書き込み中にエラーが発生しました: {e}")
        return False


def load_price_data(keyword):
    """指定されたキーワードの価格データをCSVから読み込む"""
    safe_filename_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    file_path = DATA_DIR / f"{safe_filename_keyword}.csv"
    if file_path.exists():
        try:
            df = pd.read_csv(file_path)
            missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
            if missing_cols:
                st.error(
                    f"CSVファイル {file_path.name} に必要な列がありません: {', '.join(missing_cols)}"
                )
                st.info(f"期待される列: {', '.join(EXPECTED_COLUMNS)}")
                st.info(f"現在の列: {', '.join(df.columns)}")
                return pd.DataFrame()

            if df.empty:
                # st.info(f"{file_path.name} は空です。") # データ取得前は空なので毎回表示しないようにコメントアウト
                return pd.DataFrame()

            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values(by="date")
            return df
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
        except Exception as e:
            st.error(f"{file_path.name} の読み込み中にエラーが発生しました: {e}")
            return pd.DataFrame()
    return pd.DataFrame()


def create_price_trend_chart(df, keyword, ma_short, ma_long, show_price_range):
    """Plotlyで価格動向チャートを作成する"""
    if df.empty or not all(col in df.columns for col in ["date", "average_price"]):
        # st.warning(f"「{keyword}」のチャート描画に必要なデータ（日付または平均価格）が不足しています。") # データ取得前は表示しない
        return go.Figure().update_layout(
            title=f"{keyword} - データ収集中またはデータなし"
        )

    fig = make_subplots(specs=[[{"secondary_y": False}]])

    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["average_price"],
            name="平均価格",
            mode="lines+markers",
            line=dict(color="royalblue", width=2),
        )
    )

    if show_price_range and all(
        col in df.columns for col in ["min_price", "max_price"]
    ):
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["max_price"],
                name="最高価格",
                mode="lines",
                line=dict(width=0),
                fillcolor="rgba(0,100,80,0.2)",
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["min_price"],
                name="最低価格",
                mode="lines",
                line=dict(width=0),
                fill="tonexty",
                fillcolor="rgba(0,176,246,0.2)",
                showlegend=False,
            )
        )

    if ma_short > 0 and "average_price" in df.columns and len(df) >= ma_short:
        df[f"ma_short_{ma_short}"] = (
            df["average_price"].rolling(window=ma_short, min_periods=1).mean()
        )
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df[f"ma_short_{ma_short}"],
                name=f"{ma_short}日移動平均",
                mode="lines",
                line=dict(color="orange", dash="dash"),
            )
        )

    if ma_long > 0 and "average_price" in df.columns and len(df) >= ma_long:
        df[f"ma_long_{ma_long}"] = (
            df["average_price"].rolling(window=ma_long, min_periods=1).mean()
        )
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df[f"ma_long_{ma_long}"],
                name=f"{ma_long}日移動平均",
                mode="lines",
                line=dict(color="green", dash="dot"),
            )
        )

    fig.update_layout(
        title=f"{keyword} 価格動向チャート",
        xaxis_title="日付",
        yaxis_title="価格 (円)",
        legend_title_text="凡例",
        hovermode="x unified",
        font_family="sans-serif",
    )
    fig.update_xaxes(rangeslider_visible=True)
    return fig


# === Streamlit UI ===
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# --- サイドバー ---
with st.sidebar:
    st.header("設定")
    brands_data_loaded = load_brands_cached()  # キャッシュされた関数を呼び出し

    if not brands_data_loaded:  # 万が一空の辞書やNoneが返ってきた場合
        st.error(
            "ブランド情報が読み込めませんでした。brands.jsonを確認または削除して再実行してください。"
        )
        st.stop()

    categories = list(brands_data_loaded.keys())
    if not categories:  # カテゴリが空の場合 (例: brands.json が空の {} だった場合)
        categories = ["未分類"]  # 最低限「未分類」を用意
        if "未分類" not in brands_data_loaded:
            brands_data_loaded["未分類"] = []

    selected_category = st.selectbox("カテゴリを選択", categories, key="sb_category")

    current_keyword = None

    if selected_category:
        brands_in_category = ["カテゴリ全体"] + brands_data_loaded.get(
            selected_category, []
        )
        selected_brand_option = st.selectbox(
            f"{selected_category}内のブランドを選択",
            brands_in_category,
            key=f"sb_brand_{selected_category.replace(' ', '_')}",
        )

        if selected_brand_option == "カテゴリ全体":
            current_keyword = selected_category
        else:
            current_keyword = f"{selected_category} {selected_brand_option}"

        st.markdown(f"**現在の検索対象:** `{current_keyword}`")

        if st.button(
            f"「{current_keyword}」の最新データを取得・更新",
            type="primary",
            key=f"btn_update_{current_keyword.replace(' ', '_')}",
        ):
            with st.spinner(
                f"「{current_keyword}」の価格情報をスクレイピング中...時間がかかる場合があります。"
            ):
                try:
                    prices = scrape_prices_for_keyword(current_keyword, max_items=30)
                    if prices:
                        save_daily_stats(current_keyword, prices)
                        st.success(f"「{current_keyword}」のデータを更新しました。")
                        st.rerun()
                    else:
                        st.warning(
                            f"「{current_keyword}」の価格情報が見つかりませんでした。"
                        )
                except Exception as e:
                    st.error(f"データ取得中にエラーが発生しました: {e}")

    st.markdown("---")
    st.subheader("チャート表示設定")
    ma_short_period = st.number_input(
        "短期移動平均 (日)",
        min_value=0,
        max_value=30,
        value=DEFAULT_MOVING_AVERAGE_SHORT,
        step=1,
        key="ni_ma_short",
    )
    ma_long_period = st.number_input(
        "長期移動平均 (日)",
        min_value=0,
        max_value=90,
        value=DEFAULT_MOVING_AVERAGE_LONG,
        step=1,
        key="ni_ma_long",
    )
    show_range_checkbox = st.checkbox(
        "価格範囲(最高/最低)を表示する", value=True, key="cb_show_range"
    )

    st.markdown("---")
    # --- ブランド追加機能 ---
    with st.expander("ブランド管理 (追加)"):
        st.subheader("新しいブランドの追加")

        # 追加先カテゴリの選択肢を更新するために、再度brands_dataを読み込む (キャッシュ利用)
        add_categories = list(load_brands_cached().keys())
        if not add_categories:  # brands.jsonが完全に空だったりした場合
            add_categories = ["未分類"]  # デフォルトのカテゴリ提供

        add_selected_category = st.selectbox(
            "追加先のカテゴリ", add_categories, key="add_brand_category_select"
        )
        new_brand_name_input = st.text_input(
            "追加するブランド名", key="add_brand_name_input"
        )

        if st.button("このブランドを追加", key="add_brand_button"):
            if add_selected_category and new_brand_name_input:
                new_brand_name = new_brand_name_input.strip()
                if not new_brand_name:
                    st.warning("ブランド名を入力してください。")
                else:
                    # brands.jsonを直接操作するために再度読み込む (キャッシュではない最新版)
                    try:
                        with open(BRAND_FILE, "r", encoding="utf-8") as f:
                            current_brands_for_add = json.load(f)
                    except (
                        FileNotFoundError
                    ):  # ファイルがない場合はデフォルトデータで初期化
                        current_brands_for_add = {"未分類": []}
                        if (
                            add_selected_category not in current_brands_for_add
                        ):  # 選択カテゴリがない場合も初期化
                            current_brands_for_add[add_selected_category] = []
                    except json.JSONDecodeError:
                        st.error(
                            f"{BRAND_FILE}が不正な形式です。修正するか削除してください。"
                        )
                        current_brands_for_add = None  # エラー時は処理中断

                    if current_brands_for_add is not None:
                        if add_selected_category not in current_brands_for_add:
                            current_brands_for_add[add_selected_category] = (
                                []
                            )  # カテゴリが存在しなければ作成

                        if (
                            new_brand_name
                            in current_brands_for_add[add_selected_category]
                        ):
                            st.warning(
                                f"ブランド「{new_brand_name}」はカテゴリ「{add_selected_category}」に既に存在します。"
                            )
                        else:
                            current_brands_for_add[add_selected_category].append(
                                new_brand_name
                            )
                            current_brands_for_add[
                                add_selected_category
                            ].sort()  # ブランドリストをソート

                            if save_brands_to_json(current_brands_for_add):
                                st.success(
                                    f"ブランド「{new_brand_name}」をカテゴリ「{add_selected_category}」に追加しました。"
                                )
                                # 入力欄をクリアするためにセッションステートを直接操作することもできるが、rerunで十分
                                st.rerun()  # UIを再描画してドロップダウンを更新
                            # else: # save_brands_to_json内でエラーメッセージ表示
            else:
                st.warning("追加先のカテゴリとブランド名を入力してください。")

# --- メインエリア ---
if current_keyword:
    df_prices = load_price_data(current_keyword)

    if not df_prices.empty:
        if not all(
            col in df_prices.columns
            for col in ["average_price", "count", "min_price", "max_price"]
        ):
            st.error(
                f"「{current_keyword}」のデータに必要な情報が不足しています。CSVファイルを確認してください。"
            )
            st.info(f"期待される列: {', '.join(EXPECTED_COLUMNS)}")
            st.info(f"現在の列: {', '.join(df_prices.columns)}")
        else:
            st.subheader(f"📈 「{current_keyword}」の価格動向")

            latest_data = df_prices.iloc[-1]
            delta_text = "N/A (データ1件)"
            if len(df_prices) > 1:
                if (
                    "average_price" in df_prices.iloc[-2].index
                    and pd.notna(latest_data["average_price"])
                    and pd.notna(df_prices.iloc[-2]["average_price"])
                ):
                    delta_value = (
                        latest_data["average_price"]
                        - df_prices.iloc[-2]["average_price"]
                    )
                    delta_text = f"{delta_value:,.0f} (前日比)"
                elif not (
                    "average_price" in df_prices.iloc[-2].index
                    and pd.notna(df_prices.iloc[-2]["average_price"])
                ):
                    delta_text = "N/A (前日データ不足)"

            st.metric(
                label="最新の平均価格",
                value=(
                    f"¥{latest_data['average_price']:,.0f}"
                    if pd.notna(latest_data["average_price"])
                    else "N/A"
                ),
                delta=delta_text,
            )

            cols = st.columns(3)
            with cols[0]:
                st.metric(
                    label="最新の取得件数",
                    value=(
                        f"{latest_data['count']}件"
                        if pd.notna(latest_data["count"])
                        else "N/A"
                    ),
                )
            with cols[1]:
                st.metric(
                    label="最新の最低価格",
                    value=(
                        f"¥{latest_data['min_price']:,.0f}"
                        if pd.notna(latest_data["min_price"])
                        else "N/A"
                    ),
                )
            with cols[2]:
                st.metric(
                    label="最新の最高価格",
                    value=(
                        f"¥{latest_data['max_price']:,.0f}"
                        if pd.notna(latest_data["max_price"])
                        else "N/A"
                    ),
                )

            price_chart = create_price_trend_chart(
                df_prices,
                current_keyword,
                ma_short_period,
                ma_long_period,
                show_range_checkbox,
            )
            st.plotly_chart(price_chart, use_container_width=True)

            with st.expander("生データ表示"):
                st.dataframe(df_prices.sort_values(by="date", ascending=False))
    else:
        st.info(
            f"「{current_keyword}」の表示可能なデータがまだありません。サイドバーからデータを取得してください。"
        )
else:
    st.info("サイドバーからカテゴリとブランドを選択してください。")

st.markdown("---")
st.caption(
    "このツールはメルカリの公開情報を利用しています。利用規約を遵守し、節度ある利用を心がけてください。"
)
