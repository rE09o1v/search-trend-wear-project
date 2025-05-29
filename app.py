import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from pathlib import Path
import datetime
import time
import re

# scraper.py から必要な関数と定数をインポート
# この部分は、Streamlit Cloud上で scraper.py が正しく動作することが前提です。
try:
    # scraper.py から関数と設定をインポート
    from scraper import (
        scrape_prices_for_keyword_and_site,
        save_daily_stats_for_site,
        DATA_DIR,
        SITE_CONFIGS,  # サイト設定も利用する可能性があるのでインポート
    )
    # DATA_DIR が scraper.py に存在することを確認
    if not hasattr(Path, 'joinpath') or not isinstance(DATA_DIR, Path):
        # DATA_DIR が期待通りにPathオブジェクトでない場合のフォールバック
        # 通常は scraper.py から正しくインポートされるはず
        st.warning("scraper.py から DATA_DIR を正しく読み込めませんでした。デフォルトパスを使用します。")
        CURRENT_FILE_DIR = Path(__file__).resolve().parent
        DATA_DIR = CURRENT_FILE_DIR / "data"
        DATA_DIR.mkdir(exist_ok=True)

except ImportError as e:
    st.error(f"scraper.py のインポートに失敗しました: {e}\n"
             "scraper.py が同じディレクトリに存在し、必要なライブラリ (selenium, streamlit-seleniumなど) が"
             "requirements.txt に記載され、正しくインストールされているか確認してください。")
    st.stop()
except AttributeError as e_attr:
    st.error(f"scraper.py から必要な変数のインポートに失敗しました (例: DATA_DIR): {e_attr}\n"
             "scraper.py に DATA_DIR や SITE_CONFIGS が正しく定義されているか確認してください。")
    st.stop()

APP_TITLE = "価格動向トラッカー (マルチサイト対応)"
BRAND_FILE = (
    Path(__file__).resolve().parent / "brands.json"
)  # app.py と同じ階層に brands.json
DEFAULT_MOVING_AVERAGE_SHORT = 5
DEFAULT_MOVING_AVERAGE_LONG = 20
EXPECTED_COLUMNS_BASE = [
    "date",
    "site",
    "keyword", # CSV内ではブランド名を指す
    "count",
    "average_price",
    "min_price",
    "max_price",
]

PLOTLY_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


@st.cache_data(ttl=3600) # 1時間キャッシュ
def load_brands_cached():
    """brands.json からブランドデータを読み込み、キャッシュする。ファイルがなければデフォルトを作成。"""
    if not BRAND_FILE.exists():
        st.warning(f"{BRAND_FILE} が見つかりません。サンプルを作成します。")
        # 新しいマルチサイト構造のデフォルトデータ
        default_brands_data = {
            "mercari": {
                "ストリート": ["Supreme", "Stussy"],
                "モード系": ["Yohji Yamamoto", "COMME des GARCONS"],
                "未分類": [],
            },
            "rakuma": {  # サンプルサイト
                "レディースアパレル": ["SNIDEL", "FRAY I.D"],
                "未分類": [],
            },
        }
        try:
            with open(BRAND_FILE, "w", encoding="utf-8") as f:
                json.dump(default_brands_data, f, ensure_ascii=False, indent=2)
            st.info(f"デフォルトの {BRAND_FILE.name} を作成しました。")
            return default_brands_data
        except Exception as e:
            st.error(f"デフォルトの {BRAND_FILE} の作成に失敗しました: {e}")
            return {"mercari": {"未分類": []}}  # 最低限のフォールバック
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        st.error(f"{BRAND_FILE.name} のJSON形式が正しくありません: {e}")
        return {"mercari": {"未分類": []}, "rakuma": {"未分類": []}} # フォールバック
    except Exception as e:
        st.error(f"{BRAND_FILE.name} の読み込みに失敗しました: {e}")
        return {"mercari": {"未分類": []}, "rakuma": {"未分類": []}} # フォールバック


def save_brands_to_json(brands_data):
    """ブランドデータを brands.json に保存する。"""
    try:
        with open(BRAND_FILE, "w", encoding="utf-8") as f:
            json.dump(brands_data, f, ensure_ascii=False, indent=2)
        load_brands_cached.clear() # キャッシュをクリア
        return True
    except Exception as e:
        st.error(f"{BRAND_FILE.name} への書き込み中にエラーが発生しました: {e}")
        return False


@st.cache_data(ttl=600)
def load_price_data_cached(site_name, keyword):
    safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = f"{safe_site_name}_{safe_keyword}.csv"
    file_path = DATA_DIR / file_name

    if file_path.exists():
        try:
            df = pd.read_csv(file_path)
            # EXPECTED_COLUMNS_BASE を使用してチェック
            missing_cols = [
                col for col in EXPECTED_COLUMNS_BASE if col not in df.columns
            ]
            if missing_cols:
                st.warning(f"{file_name} に必要な列が不足しています: {missing_cols}。データを読み込めません。")
                return pd.DataFrame(columns=EXPECTED_COLUMNS_BASE)
            if df.empty:
                return pd.DataFrame(columns=EXPECTED_COLUMNS_BASE)
            
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values(by="date")
            return df
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def create_multi_brand_price_trend_chart(
    dataframes_dict, # {'display_key': {'df': DataFrame, 'site': str, 'brand_keyword': str}, ...}
    ma_short,
    ma_long,
    show_price_range_for_primary=None,
    primary_full_keyword=None,
):
    """複数のブランド/サイトの価格トレンドチャートを作成する。"""
    if not dataframes_dict:
        fig = go.Figure()
        fig.update_layout(
            title="表示するデータが選択されていません",
            xaxis_title="日付",
            yaxis_title="価格 (円)",
            font_family="sans-serif",
        )
        return fig

    fig = make_subplots(specs=[[{"secondary_y": False}]]) # 単一Y軸
    color_idx = 0

    for full_kw, df_data in dataframes_dict.items():
        site, keyword = df_data["site"], df_data["keyword"]  # 表示名に使う
        df = df_data["df"]

        if df.empty or "average_price" not in df.columns or df["average_price"].isnull().all():
            continue

        current_color = PLOTLY_COLORS[color_idx % len(PLOTLY_COLORS)]
        display_name = f"{site}: {keyword}"

        # 平均価格のプロット
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["average_price"],
                name=f"{display_name} 平均",
                mode="lines+markers",
                line=dict(color=current_color, width=2),
                marker=dict(size=4),
            )
        )

        # 価格範囲の表示 (プライマリターゲットのみ)
        if (
            show_price_range_for_primary
            and full_kw == primary_full_keyword
            and all(c in df.columns for c in ["min_price", "max_price"])
        ):
            try:  # 色コード変換エラー対策
                r, g, b = (
                    int(current_color[1:3], 16),
                    int(current_color[3:5], 16),
                    int(current_color[5:7], 16),
                )
                fill_rgba = f"rgba({r},{g},{b},0.1)"
                fig.add_trace(
                    go.Scatter(
                        x=df["date"],
                        y=df["max_price"],
                        mode="lines",
                        line=dict(width=0), # 線は非表示
                        fillcolor=fill_rgba,
                        showlegend=False,
                        hoverinfo='skip', # ホバー情報なし
                    )
                )
                fig.add_trace(
                    go.Scatter(
                        x=df["date"],
                        y=df["min_price"],
                        mode="lines",
                        line=dict(width=0), # 線は非表示
                        fill="tonexty",  # 上のトレース（max_price）まで塗りつぶす
                        fillcolor=fill_rgba,
                        name=f"{legend_name_prefix} 価格範囲", # 凡例に表示
                        showlegend=True, # 価格範囲の凡例は表示
                        hoverinfo='skip',
                    )
                )
            except ValueError:
                pass  # 色変換に失敗した場合はバンド表示をスキップ

        # 短期移動平均
        if ma_short > 0 and len(df) >= ma_short:
            df[f"ma_short"] = df["average_price"].rolling(window=ma_short, min_periods=1).mean()
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[f"ma_short"],
                    name=f"{display_name} {ma_short}日MA",
                    mode="lines",
                    line=dict(color=current_color, dash="dash", width=1.5),
                    opacity=0.8,
                )
            )
        # 長期移動平均
        if ma_long > 0 and len(df) >= ma_long:
            df[f"ma_long"] = df["average_price"].rolling(window=ma_long, min_periods=1).mean()
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[f"ma_long"],
                    name=f"{display_name} {ma_long}日MA",
                    mode="lines",
                    line=dict(color=current_color, dash="dot", width=1.5),
                    opacity=0.8,
                )
            )
        color_idx += 1

    fig.update_layout(
        title="価格動向チャート (複数サイト/ブランド対応)",
        xaxis_title="日付",
        yaxis_title="価格 (円)",
        legend_title_text="サイト: ブランド/指標",
        hovermode="x unified",
        font_family="sans-serif",
        height=600,
        margin=dict(l=50, r=50, t=80, b=50), # チャートのマージン調整
    )
    fig.update_xaxes(rangeslider_visible=True) # 日付範囲スライダー
    return fig


# --- Streamlit UI ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

# セッションステートの初期化
if "selected_targets_for_chart" not in st.session_state:
    st.session_state.selected_targets_for_chart = []
if "last_active_target_for_update" not in st.session_state:
    st.session_state.last_active_target_for_update = (
        None  # {'site': str, 'keyword': str}
    )

with st.sidebar:
    st.header("⚙️ 設定")
    
    # ブランドデータの読み込み
    brands_data_all_sites = load_brands_cached()
    if not brands_data_all_sites or not any(brands_data_all_sites.values()):
        st.error(f"{BRAND_FILE.name} からブランド情報が読み込めませんでした。または、内容が空です。")
        st.markdown(f"`{BRAND_FILE.name}` にサイトとブランドを登録してください。例:")
        st.code("""
{
  "mercari": {
    "カテゴリ名1": ["ブランドA", "ブランドB"],
    "未分類": ["ブランドC"]
  },
  "rakuma": {
    "カテゴリ名X": ["ブランドD"]
  }
}
        """, language="json")
        st.stop()

    available_sites_from_brands = list(brands_data_all_sites.keys())
    if not available_sites_from_brands:
        st.error(f"{BRAND_FILE.name} に監視対象サイトが設定されていません。")
        st.stop()

    # --- ブランド選択 ---
    st.subheader("表示ブランド選択")
    # どのサイトのブランドリストを表示するか選択
    selected_site_for_sidebar_display = st.selectbox(
        "操作対象サイト",
        available_sites_from_brands,
        index=0, # デフォルトで最初のサイトを選択
        key="sb_site_sidebar_display"
    )

    st.subheader(f"「{selected_site_for_display}」の表示ブランド選択")

    current_brands_on_site = brands_data_all_sites.get(selected_site_for_display, {})
    temp_selected_targets = list(
        st.session_state.selected_targets_for_chart
    )  # 現在の選択をコピー

    for category, brands_in_cat in current_brands_on_site.items():
        with st.expander(f"{category} ({len(brands_in_cat)})", expanded=False):
            for brand_name in brands_in_cat:
                # フルキーワードはサイト名も含めて一意にする
                # keyword_for_scrape はカテゴリ名とブランド名 (例: "ストリート Supreme")
                # display_brand_name はブランド名のみ (例: "Supreme")
                keyword_for_scrape = (
                    f"{category} {brand_name}" if category != "未分類" else brand_name
                )
                full_target_key = f"{selected_site_for_display}::{keyword_for_scrape}"  # セッションステート用の一意なキー

                checkbox_key = (
                    f"cb_target_{full_target_key.replace(' ', '_').replace('::','__')}"
                )

                if checkbox_key not in st.session_state:
                    st.session_state[checkbox_key] = False

                is_checked = st.checkbox(f"{brand_name} ({category})", key=checkbox_key)

                current_target_obj = {
                    "site": selected_site_for_display,
                    "keyword": keyword_for_scrape,
                    "display": f"{selected_site_for_display}: {keyword_for_scrape}",
                }

                if is_checked:
                    if not any(
                        t["display"] == current_target_obj["display"]
                        for t in temp_selected_targets
                    ):
                        temp_selected_targets.append(current_target_obj)
                    st.session_state.last_active_target_for_update = current_target_obj
                else:
                    temp_selected_targets = [
                        t
                        for t in temp_selected_targets
                        if t["display"] != current_target_obj["display"]
                    ]

    st.session_state.selected_targets_for_chart = temp_selected_targets

    if st.session_state.selected_targets_for_chart:
        st.markdown(
            f"**チャート表示対象 ({len(st.session_state.selected_targets_for_chart)}件):**"
        )
        for t in st.session_state.selected_targets_for_chart[:5]:
            st.markdown(f"- `{t['display']}`")
        if len(st.session_state.selected_targets_for_chart) > 5:
            st.markdown("  ...")
    else:
        st.markdown("チャートに表示するブランドを選択してください。")

    st.markdown("---")

    # --- データ取得ボタン ---
    if st.session_state.last_active_target_for_update:
        active_target = st.session_state.last_active_target_for_update
        btn_label = f"「{active_target['display']}」のデータを取得・更新"
        if st.button(btn_label, type="primary", key=f"btn_update_active_target"):
            with st.spinner(
                f"「{active_target['display']}」の価格情報をスクレイピング中..."
            ):
                try:
                    prices = scrape_prices_for_keyword_and_site(
                        active_target["site"],
                        active_target["keyword"],
                        max_items_override=SITE_CONFIGS.get(
                            active_target["site"], {}
                        ).get("max_items_to_scrape", 30),
                    )
                    if prices:
                        save_daily_stats_for_site(
                            active_target["site"], active_target["keyword"], prices
                        )
                        st.success(
                            f"「{active_target['display']}」のデータを更新しました。"
                        )
                        load_price_data_cached.clear()  # 全体キャッシュクリア (または個別クリア)
                        st.rerun()
                    else:
                        st.warning(
                            f"「{active_target['display']}」の価格情報が見つかりませんでした。"
                        )
                except Exception as e:
                    st.error(f"データ取得中にエラーが発生しました: {e}")
    else:
        st.info("ブランドを選択すると、そのブランドのデータ更新ボタンが表示されます。")

    st.markdown("---")
    # --- チャート表示設定 ---
    st.subheader("チャート表示設定")
    ma_short_period = st.number_input(
        "短期移動平均 (日)",
        0,
        30,
        DEFAULT_MOVING_AVERAGE_SHORT,
        1,
        key="ni_ma_short_multi_site",
    )
    ma_long_period = st.number_input(
        "長期移動平均 (日)",
        0,
        90,
        DEFAULT_MOVING_AVERAGE_LONG,
        1,
        key="ni_ma_long_multi_site",
    )

    show_range_option_multi = False
    primary_target_for_band = None
    if st.session_state.last_active_target_for_update and any(
        t["display"] == st.session_state.last_active_target_for_update["display"]
        for t in st.session_state.selected_targets_for_chart
    ):
        primary_target_for_band = st.session_state.last_active_target_for_update[
            "display"
        ]
        show_range_option_multi = st.checkbox(
            f"「{primary_target_for_band}」の価格範囲を表示",
            value=False,
            key="cb_show_range_multi_site",
        )

    st.markdown("---")
    # --- ブランド管理 ---
    with st.expander("ブランド管理 (追加/削除)", expanded=False):
        st.subheader("新しいブランドの追加")

        add_sites = list(load_brands_cached().keys())
        if not add_sites:
            add_sites = ["mercari"]  # フォールバック
        add_selected_site = st.selectbox(
            "追加先のサイト", add_sites, key="add_brand_site_sel"
        )

        # サイト内のカテゴリを取得
        site_categories = list(
            load_brands_cached().get(add_selected_site, {"未分類": []}).keys()
        )
        if not site_categories:
            site_categories = ["未分類"]

        add_selected_category = st.selectbox(
            "追加先のカテゴリ", site_categories, key="add_brand_cat_sel_multi_site"
        )
        new_brand_name_input = st.text_input(
            "追加するブランド名", key="add_brand_name_in_multi_site"
        )

        if st.button("このブランドを追加", key="add_brand_btn_multi_site"):
            if add_selected_site and add_selected_category and new_brand_name_input:
                new_brand_name = new_brand_name_input.strip()
                if not new_brand_name:
                    st.warning("ブランド名を入力してください。")
                else:
                    all_brands_data = load_brands_cached()  # 最新のデータを取得
                    if add_selected_site not in all_brands_data:
                        all_brands_data[add_selected_site] = {}
                    if add_selected_category not in all_brands_data[add_selected_site]:
                        all_brands_data[add_selected_site][add_selected_category] = []

                    if (
                        new_brand_name
                        in all_brands_data[add_selected_site][add_selected_category]
                    ):
                        st.warning(
                            f"ブランド「{new_brand_name}」はサイト「{add_selected_site}」のカテゴリ「{add_selected_category}」に既に存在します。"
                        )
                    else:
                        all_brands_data[add_selected_site][
                            add_selected_category
                        ].append(new_brand_name)
                        all_brands_data[add_selected_site][add_selected_category].sort()
                        if save_brands_to_json(all_brands_data):
                            st.success(
                                f"ブランド「{new_brand_name}」をサイト「{add_selected_site}」のカテゴリ「{add_selected_category}」に追加しました。"
                            )
                            st.rerun()
            else:
                st.caption(f"サイト「{del_selected_site}」にカテゴリはありません。")
        else:
            st.caption("削除できるブランド情報がありません。")


# --- メインコンテンツエリア ---
if st.session_state.selected_targets_for_chart:
    dataframes_to_plot_dict = {}
    any_data_loaded_for_chart = False
    for target in st.session_state.selected_targets_for_chart:
        df = load_price_data_cached(target["site"], target["keyword"])
        if not df.empty:
            # 辞書のキーには一意な target['display'] を使う
            dataframes_to_plot_dict[target["display"]] = {
                "df": df,
                "site": target["site"],
                "keyword": target["keyword"],
            }
            any_data_loaded_for_chart = True

            if target["display"] == (
                st.session_state.last_active_target_for_update or {}
            ).get("display"):
                st.subheader(f"📊 「{target['display']}」の最新情報")
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

    if any_data_loaded_for_chart:
        price_chart = create_multi_brand_price_trend_chart(
            dataframes_to_plot_dict,
            ma_short_period,
            ma_long_period,
            show_price_range_for_primary=show_range_option_multi,
            primary_full_keyword=primary_target_for_band,
        )
        st.plotly_chart(price_chart_main, use_container_width=True)

        with st.expander("選択ブランドの生データ表示 (各最新50件)"):
            for display_key, data_dict in dataframes_to_plot_dict.items():
                st.markdown(f"**{display_key}**")
                st.dataframe(data_dict["df"].sort_values(by="date", ascending=False).head(50))
    elif st.session_state.selected_targets_for_chart: # ターゲットは選択されているがデータがない場合
        st.info(
            "選択されたブランドの価格データがまだありません。\n"
            "サイドバーでブランドを選択し、「データ取得/更新」ボタンを押してデータを収集してください。"
        )
    else: # ターゲットも選択されていない場合 (この分岐はサイドバーのメッセージと重複するが念のため)
        st.info("サイドバーから表示したいブランドを1つ以上選択してください。")
else:
    st.info("サイドバーから表示したいブランドを1つ以上選択してください。")


st.markdown("---")
st.caption(
    "このツールは各ECサイトの公開情報を利用しています。"
    "各サイトの利用規約を遵守し、節度ある利用を心がけてください。"
    "スクレイピング処理には時間がかかることがあります。"
)
