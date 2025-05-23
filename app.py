import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path
import datetime
import time  # st.spinner のデモ用
import re

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
]

# --- 色のリスト (複数のブランド表示用) ---
PLOTLY_COLORS = [
    "#1f77b4",  # Muted blue
    "#ff7f0e",  # Safety orange
    "#2ca02c",  # Cooked asparagus green
    "#d62728",  # Brick red
    "#9467bd",  # Muted purple
    "#8c564b",  # Chestnut brown
    "#e377c2",  # Raspberry yogurt pink
    "#7f7f7f",  # Middle gray
    "#bcbd22",  # Curry yellow-green
    "#17becf",  # Blue-teal
]


@st.cache_data(ttl=3600)
def load_brands_cached():
    if not BRAND_FILE.exists():
        st.warning(f"{BRAND_FILE} が見つかりません。サンプルを作成します。")
        default_brands_data = {
            "ストリート": ["Supreme", "Stussy", "A BATHING APE"],
            "モード系": ["Yohji Yamamoto", "COMME des GARCONS", "ISSEY MIYAKE"],
            "アウトドア": ["THE NORTH FACE", "Patagonia", "Arc'teryx"],
            "スニーカー": ["NIKE Air Jordan", "NIKE Dunk", "adidas Yeezy Boost"],
            "未分類": [],
        }
        try:
            with open(BRAND_FILE, "w", encoding="utf-8") as f:
                json.dump(default_brands_data, f, ensure_ascii=False, indent=2)
            st.info(f"デフォルトの {BRAND_FILE} を作成しました。")
            return default_brands_data
        except Exception as e:
            st.error(f"デフォルトの {BRAND_FILE} の作成に失敗しました: {e}")
            return {"未分類": []}
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        st.error(f"{BRAND_FILE} のJSON形式が正しくありません: {e}")
        return {"未分類": []}
    except Exception as e:
        st.error(f"{BRAND_FILE} の読み込みに失敗しました: {e}")
        return {"未分類": []}


def save_brands_to_json(brands_data):
    try:
        with open(BRAND_FILE, "w", encoding="utf-8") as f:
            json.dump(brands_data, f, ensure_ascii=False, indent=2)
        load_brands_cached.clear()
        return True
    except Exception as e:
        st.error(f"brands.jsonへの書き込み中にエラーが発生しました: {e}")
        return False


@st.cache_data(ttl=600)  # 読み込みデータを10分キャッシュ
def load_price_data_cached(keyword):
    safe_filename_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    file_path = DATA_DIR / f"{safe_filename_keyword}.csv"
    if file_path.exists():
        try:
            df = pd.read_csv(file_path)
            missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
            if missing_cols:
                # st.warning(f"CSV {file_path.name} に列不足: {', '.join(missing_cols)}") # 毎回表示されるとうるさいのでコメントアウト
                return pd.DataFrame()
            if df.empty:
                return pd.DataFrame()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values(by="date")
            return df
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
        except Exception:
            return pd.DataFrame()  # エラー時は空を返す
    return pd.DataFrame()


def create_multi_brand_price_trend_chart(
    dataframes_dict,
    ma_short,
    ma_long,
    show_price_range_for_primary=None,
    primary_keyword=None,
):
    if not dataframes_dict:
        return go.Figure().update_layout(title="表示するデータが選択されていません")

    fig = make_subplots(specs=[[{"secondary_y": False}]])

    color_idx = 0
    for keyword, df in dataframes_dict.items():
        if df.empty or "average_price" not in df.columns:
            continue

        current_color = PLOTLY_COLORS[color_idx % len(PLOTLY_COLORS)]

        # 平均価格のライン
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["average_price"],
                name=f"{keyword} 平均",
                mode="lines+markers",
                line=dict(color=current_color, width=2),
            )
        )

        # 価格範囲のバンド表示 (プライマリキーワードのみ、またはオプションで選択されたもののみ)
        if (
            show_price_range_for_primary
            and keyword == primary_keyword
            and all(col in df.columns for col in ["min_price", "max_price"])
        ):
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df["max_price"],
                    mode="lines",
                    line=dict(width=0),
                    showlegend=False,
                    fillcolor=f"rgba({int(current_color[1:3],16)},{int(current_color[3:5],16)},{int(current_color[5:7],16)},0.1)",  # 色を薄く
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df["min_price"],
                    mode="lines",
                    line=dict(width=0),
                    showlegend=False,
                    fill="tonexty",
                    fillcolor=f"rgba({int(current_color[1:3],16)},{int(current_color[3:5],16)},{int(current_color[5:7],16)},0.1)",
                )
            )

        # 移動平均線 (各ブランドごと)
        if ma_short > 0 and len(df) >= ma_short:
            df[f"ma_short_{ma_short}"] = (
                df["average_price"].rolling(window=ma_short, min_periods=1).mean()
            )
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[f"ma_short_{ma_short}"],
                    name=f"{keyword} {ma_short}日MA",
                    mode="lines",
                    line=dict(color=current_color, dash="dash"),
                    opacity=0.7,
                )
            )
        if ma_long > 0 and len(df) >= ma_long:
            df[f"ma_long_{ma_long}"] = (
                df["average_price"].rolling(window=ma_long, min_periods=1).mean()
            )
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[f"ma_long_{ma_long}"],
                    name=f"{keyword} {ma_long}日MA",
                    mode="lines",
                    line=dict(color=current_color, dash="dot"),
                    opacity=0.7,
                )
            )
        color_idx += 1

    fig.update_layout(
        title="価格動向チャート (複数ブランド)",
        xaxis_title="日付",
        yaxis_title="価格 (円)",
        legend_title_text="ブランド/指標",
        hovermode="x unified",
        font_family="sans-serif",
    )
    fig.update_xaxes(rangeslider_visible=True)
    return fig


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# --- セッションステート初期化 ---
if "selected_brands_for_chart" not in st.session_state:
    st.session_state.selected_brands_for_chart = []
if (
    "last_active_keyword_for_update" not in st.session_state
):  # データ更新対象のキーワード
    st.session_state.last_active_keyword_for_update = None

# --- サイドバー ---
with st.sidebar:
    st.header("設定")
    brands_data_loaded = load_brands_cached()

    if not brands_data_loaded:
        st.error("ブランド情報が読み込めませんでした。")
        st.stop()

    st.subheader("表示ブランド選択")
    # 選択されたブランドをセッションステートで管理
    temp_selected_brands = []

    for category, brands_in_cat in brands_data_loaded.items():
        with st.expander(
            f"{category} ({len(brands_in_cat)})", expanded=False
        ):  # 最初は閉じておく
            # カテゴリ全体のチェックボックス (オプション)
            # cat_key = f"cb_cat_{category.replace(' ', '_')}"
            # if st.checkbox(f"{category} 全体", key=cat_key, value=(category in st.session_state.selected_brands_for_chart)):
            #     if category not in temp_selected_brands: temp_selected_brands.append(category)
            # else:
            #     if category in temp_selected_brands: temp_selected_brands.remove(category)

            for brand_name in brands_in_cat:
                keyword_display = (
                    f"{brand_name}"  # カテゴリ名は含めずに表示 (凡例で見やすくするため)
                )
                # session_state に保存するキーはフルパスが良い (カテゴリ + ブランド名)
                full_keyword = f"{category} {brand_name}"

                checkbox_key = (
                    f"cb_brand_{full_keyword.replace(' ', '_').replace('/', '_')}"
                )

                # st.session_stateにキーがなければ初期化 (False)
                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = False

                is_checked = st.checkbox(keyword_display, key=checkbox_key)
                if is_checked:
                    if full_keyword not in temp_selected_brands:
                        temp_selected_brands.append(full_keyword)
                    st.session_state.last_active_keyword_for_update = (
                        full_keyword  # 最後に操作したものを更新対象候補に
                    )
                # チェックが外された場合の処理はStreamlitがキー経由でハンドリング

    # 実際の選択リストを更新 (チェックボックスのオンオフで Streamlit が再実行されるたびに更新される)
    st.session_state.selected_brands_for_chart = temp_selected_brands

    if st.session_state.selected_brands_for_chart:
        st.markdown(
            f"**チャート表示対象 ({len(st.session_state.selected_brands_for_chart)}件):**"
        )
        for kw in st.session_state.selected_brands_for_chart[:5]:  # 最大5件表示
            st.markdown(f"- `{kw}`")
        if len(st.session_state.selected_brands_for_chart) > 5:
            st.markdown("  ...")
    else:
        st.markdown("チャートに表示するブランドを選択してください。")

    st.markdown("---")
    # データ更新は、最後に操作したアクティブなキーワードに対して行う
    if st.session_state.last_active_keyword_for_update:
        active_kw_for_update = st.session_state.last_active_keyword_for_update
        if st.button(
            f"「{active_kw_for_update}」のデータを取得・更新",
            type="primary",
            key=f"btn_update_active",
        ):
            with st.spinner(
                f"「{active_kw_for_update}」の価格情報をスクレイピング中..."
            ):
                try:
                    prices = scrape_prices_for_keyword(
                        active_kw_for_update, max_items=30
                    )
                    if prices:
                        save_daily_stats(active_kw_for_update, prices)
                        st.success(
                            f"「{active_kw_for_update}」のデータを更新しました。"
                        )
                        load_price_data_cached.clear()  # このキーワードのキャッシュをクリア
                        st.rerun()
                    else:
                        st.warning(
                            f"「{active_kw_for_update}」の価格情報が見つかりませんでした。"
                        )
                except Exception as e:
                    st.error(f"データ取得中にエラーが発生しました: {e}")
    else:
        st.info("ブランドを選択するとデータ更新ボタンが表示されます。")

    st.markdown("---")
    st.subheader("チャート表示設定")
    ma_short_period = st.number_input(
        "短期移動平均 (日)",
        0,
        30,
        DEFAULT_MOVING_AVERAGE_SHORT,
        1,
        key="ni_ma_short_multi",
    )
    ma_long_period = st.number_input(
        "長期移動平均 (日)",
        0,
        90,
        DEFAULT_MOVING_AVERAGE_LONG,
        1,
        key="ni_ma_long_multi",
    )

    # 複数ブランド表示時は、価格範囲はプライマリ（最後に操作した or 最初の）ものだけにするか、全非表示が良い
    # ここでは、最後に操作した (st.session_state.last_active_keyword_for_update) ブランドの価格範囲を表示するオプション
    show_range_option = False
    if (
        st.session_state.last_active_keyword_for_update
        in st.session_state.selected_brands_for_chart
    ):
        show_range_option = st.checkbox(
            f"「{st.session_state.last_active_keyword_for_update}」の価格範囲を表示",
            value=False,
            key="cb_show_range_multi",
        )

    st.markdown("---")
    with st.expander("ブランド管理 (追加)"):
        st.subheader("新しいブランドの追加")
        add_categories = list(load_brands_cached().keys())
        if not add_categories:
            add_categories = ["未分類"]
        add_selected_category = st.selectbox(
            "追加先のカテゴリ", add_categories, key="add_brand_cat_sel_multi"
        )
        new_brand_name_input = st.text_input(
            "追加するブランド名", key="add_brand_name_in_multi"
        )

        if st.button("このブランドを追加", key="add_brand_btn_multi"):
            if add_selected_category and new_brand_name_input:
                new_brand_name = new_brand_name_input.strip()
                if not new_brand_name:
                    st.warning("ブランド名を入力してください。")
                else:
                    try:
                        with open(BRAND_FILE, "r", encoding="utf-8") as f:
                            current_brands_for_add = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        current_brands_for_add = {"未分類": []}

                    if add_selected_category not in current_brands_for_add:
                        current_brands_for_add[add_selected_category] = []

                    if new_brand_name in current_brands_for_add[add_selected_category]:
                        st.warning(
                            f"ブランド「{new_brand_name}」はカテゴリ「{add_selected_category}」に既に存在します。"
                        )
                    else:
                        current_brands_for_add[add_selected_category].append(
                            new_brand_name
                        )
                        current_brands_for_add[add_selected_category].sort()
                        if save_brands_to_json(current_brands_for_add):
                            st.success(
                                f"ブランド「{new_brand_name}」をカテゴリ「{add_selected_category}」に追加。"
                            )
                            st.rerun()
            else:
                st.warning("追加先のカテゴリとブランド名を入力してください。")

# --- メインエリア ---
if st.session_state.selected_brands_for_chart:
    dataframes_to_plot = {}
    any_data_loaded = False
    for keyword in st.session_state.selected_brands_for_chart:
        df = load_price_data_cached(keyword)  # キャッシュされた関数を使用
        if not df.empty:
            dataframes_to_plot[keyword] = df
            any_data_loaded = True
            # 最新統計情報の表示 (プライマリの物だけ、または選択されたもの全てループ)
            # ここでは st.session_state.last_active_keyword_for_update の情報を表示
            if keyword == st.session_state.last_active_keyword_for_update:
                st.subheader(f"📊 「{keyword}」の最新情報")
                latest_data = df.iloc[-1]
                delta_text = "N/A"
                if (
                    len(df) > 1
                    and "average_price" in df.iloc[-2].index
                    and pd.notna(latest_data["average_price"])
                    and pd.notna(df.iloc[-2]["average_price"])
                ):
                    delta_value = (
                        latest_data["average_price"] - df.iloc[-2]["average_price"]
                    )
                    delta_text = f"{delta_value:,.0f} (前日比)"
                st.metric(
                    label="最新平均価格",
                    value=(
                        f"¥{latest_data['average_price']:,.0f}"
                        if pd.notna(latest_data["average_price"])
                        else "N/A"
                    ),
                    delta=delta_text,
                )
                # 他のメトリクスも表示する場合はここに

    if any_data_loaded:
        # show_range_for_primary_kw = st.session_state.last_active_keyword_for_update if show_range_option else None
        price_chart = create_multi_brand_price_trend_chart(
            dataframes_to_plot,
            ma_short_period,
            ma_long_period,
            show_price_range_for_primary=show_range_option,  # チェックボックスの値
            primary_keyword=st.session_state.last_active_keyword_for_update,  # バンド表示対象
        )
        st.plotly_chart(price_chart, use_container_width=True)

        with st.expander("選択ブランドの生データ表示 (最新50件)"):
            for kw, df_kw in dataframes_to_plot.items():
                st.markdown(f"**{kw}**")
                st.dataframe(df_kw.sort_values(by="date", ascending=False).head(50))
    else:
        st.info(
            "選択されたブランドのデータがまだありません。サイドバーでブランドを選択し、必要に応じてデータを取得してください。"
        )
else:
    st.info("サイドバーから表示したいブランドを1つ以上選択してください。")

st.markdown("---")
st.caption(
    "このツールはメルカリの公開情報を利用しています。利用規約を遵守し、節度ある利用を心がけてください。"
)
