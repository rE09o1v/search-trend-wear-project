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
    from scraper import (
        scrape_prices_for_keyword_and_site,
        save_daily_stats_for_site,
        DATA_DIR, # scraper.py で定義されたDATA_DIRを使用
        SITE_CONFIGS, # scraper.py で定義されたSITE_CONFIGSを使用
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
BRAND_FILE = Path(__file__).resolve().parent / "brands.json"
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
        st.warning(f"{BRAND_FILE.name} が見つかりません。サンプルデータで作成します。")
        default_brands_data = {
            "mercari": {
                "ストリート": ["Supreme", "Stussy"],
                "モード系": ["Yohji Yamamoto", "COMME des GARCONS"],
                "未分類": [],
            },
            "rakuma": {
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
            st.error(f"デフォルトの {BRAND_FILE.name} の作成に失敗しました: {e}")
            # フォールバックとして最小限のデータを返す
            return {"mercari": {"未分類": []}, "rakuma": {"未分類": []}}

    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            content = f.read()
            if not content:
                st.warning(f"{BRAND_FILE} は空です。サンプルデータで初期化します。")
                return {}
            return json.loads(content)
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
def load_price_data_cached(site_name, brand_keyword):
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = f"{safe_site_name}_{safe_brand_keyword}.csv"
    file_path = DATA_DIR / file_name

    if file_path.exists():
        try:
            df = pd.read_csv(file_path)
            # 期待される列が存在するか確認
            missing_cols = [col for col in EXPECTED_COLUMNS_BASE if col not in df.columns]
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
    show_price_range_for_primary=None, # プライマリターゲットの表示名
    primary_target_for_band_display=None, # プライマリターゲットの表示名 (show_price_range_for_primary と同じものを期待)
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

    for target_display_key, df_data in dataframes_dict.items():
        df = df_data["df"]
        site_name = df_data["site"]
        brand_name = df_data["brand_keyword"]

        if df.empty or "average_price" not in df.columns or df["average_price"].isnull().all():
            continue

        current_color = PLOTLY_COLORS[color_idx % len(PLOTLY_COLORS)]
        legend_name_prefix = f"{site_name}: {brand_name}"

        # 平均価格のプロット
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["average_price"],
                name=f"{legend_name_prefix} 平均",
                mode="lines+markers",
                line=dict(color=current_color, width=2),
                marker=dict(size=4),
            )
        )

        # 価格範囲の表示 (プライマリターゲットのみ)
        if (
            show_price_range_for_primary and
            target_display_key == primary_target_for_band_display and
            all(c in df.columns for c in ["min_price", "max_price"]) and
            not df["min_price"].isnull().all() and
            not df["max_price"].isnull().all()
        ):
            try:
                # HEXからRGBAへ変換
                r, g, b = tuple(int(current_color.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
                fill_rgba = f"rgba({r},{g},{b},0.1)" # 透明度0.1
                
                # 上限と下限の価格範囲を塗りつぶしで表示
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
            except ValueError: # 色変換エラーの場合
                pass # 価格範囲の表示をスキップ

        # 短期移動平均
        if ma_short > 0 and len(df) >= ma_short:
            df[f"ma_short"] = df["average_price"].rolling(window=ma_short, min_periods=1).mean()
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[f"ma_short"],
                    name=f"{legend_name_prefix} {ma_short}日MA",
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
                    name=f"{legend_name_prefix} {ma_long}日MA",
                    mode="lines",
                    line=dict(color=current_color, dash="dot", width=1.5),
                    opacity=0.8,
                )
            )
        color_idx += 1

    fig.update_layout(
        title_text="価格動向チャート",
        xaxis_title_text="日付",
        yaxis_title_text="価格 (円)",
        legend_title_text="凡例",
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
    st.session_state.last_active_target_for_update = None

with st.sidebar:
    st.header("⚙️ 設定")
    
    # ブランドデータの読み込み
    brands_data_all_sites = load_brands_cached()

    if not brands_data_all_sites:
        st.error(
            "ブランド情報が読み込めませんでした。brands.jsonが空か、または存在しない可能性があります。"
        )
        if BRAND_FILE.exists() and BRAND_FILE.read_text() == "":
            load_brands_cached.clear()
            brands_data_all_sites = load_brands_cached()
            if not brands_data_all_sites:
                st.stop()
        elif not BRAND_FILE.exists():
            brands_data_all_sites = load_brands_cached()
            if not brands_data_all_sites:
                st.stop()
        else:
            st.stop()

    available_sites_from_brands = list(brands_data_all_sites.keys())
    if not available_sites_from_brands:
        st.error(f"{BRAND_FILE.name} に監視対象サイトが設定されていません。")
        st.stop()

    selected_site_for_display = st.selectbox(
        "表示/操作するサイトを選択", available_sites, key="sb_site_display_v3"
    )

    st.subheader(f"「{selected_site_for_display}」の表示ブランド選択")

    current_brands_on_site = brands_data_all_sites.get(selected_site_for_display, {})
    # This list will be rebuilt in each run based on current checkbox states
    current_run_selected_targets = []

    for category, brands_in_cat in current_brands_on_site.items():
        with st.expander(f"{category} ({len(brands_in_cat)})", expanded=False):
            for brand_name_from_json in brands_in_cat:
                target_obj = {
                    "site": selected_site_for_display,
                    "brand_keyword": brand_name_from_json,
                    "display_name": f"{selected_site_for_display}: {brand_name_from_json}",
                    "category_for_json": category,
                }
                checkbox_key = f"cb_target_{target_obj['display_name'].replace(' ', '_').replace('::','__').replace(':','_')}"

                # Determine the initial value for the checkbox if its state is not yet set.
                # This should come from the persisted st.session_state.selected_targets_for_chart
                initial_checked_state = any(
                    t["display_name"] == target_obj["display_name"]
                    for t in st.session_state.selected_targets_for_chart
                )

                # The 'value' param is only used if checkbox_key is not in st.session_state.
                # Otherwise, st.session_state[checkbox_key] (the widget's own state) is used.
                is_checked_now = st.checkbox(
                    f"{brand_name_from_json}",
                    value=initial_checked_state,  # Used for first render if key not in session_state
                    key=checkbox_key,
                )
                # At this point, st.session_state[checkbox_key] is correctly set by Streamlit.

                if is_checked_now:
                    current_run_selected_targets.append(target_obj)
                    st.session_state.last_active_target_for_update = target_obj

    # After iterating through all checkboxes, compare the newly built list
    # with the persisted list in session_state. If they differ, update and rerun.
    current_sel_display_names = {
        t["display_name"] for t in st.session_state.selected_targets_for_chart
    }
    new_sel_display_names = {t["display_name"] for t in current_run_selected_targets}

    if current_sel_display_names != new_sel_display_names:
        st.session_state.selected_targets_for_chart = current_run_selected_targets
        st.rerun()

    if st.session_state.selected_targets_for_chart:
        st.markdown(f"**チャート表示対象 ({len(st.session_state.selected_targets_for_chart)}件):**")
        for t in st.session_state.selected_targets_for_chart[:5]: # 最大5件表示
            st.markdown(f"- `{t['display_name']}`")
        if len(st.session_state.selected_targets_for_chart) > 5:
            st.markdown("  ...")
    else:
        st.markdown("チャートに表示するブランドを選択してください。")

    st.markdown("---")
    if st.session_state.selected_targets_for_chart:
        if st.button("選択した全ブランドのデータを取得・更新", key="btn_bulk_update"):
            targets_to_scrape = list(st.session_state.selected_targets_for_chart)
            total_targets = len(targets_to_scrape)
            success_count = 0
            failure_count = 0

            progress_bar = st.progress(0)
            status_text = st.empty()

            with st.spinner(
                f"選択した {total_targets} 件のブランドデータを一括取得中..."
            ):
                for i, target in enumerate(targets_to_scrape):
                    status_text.info(
                        f"処理中 ({i+1}/{total_targets}): 「{target['display_name']}」..."
                    )
                    try:
                        prices = scrape_prices_for_keyword_and_site(
                            target["site"],
                            target["brand_keyword"],
                            max_items_override=SITE_CONFIGS.get(target["site"], {}).get(
                                "max_items_to_scrape", 30
                            ),
                        )
                        if prices:
                            save_daily_stats_for_site(
                                target["site"], target["brand_keyword"], prices
                            )
                            st.write(
                                f"✅ 「{target['display_name']}」のデータを更新しました。"
                            )
                            success_count += 1
                        else:
                            st.write(
                                f"⚠️ 「{target['display_name']}」の価格情報が見つかりませんでした。"
                            )
                            failure_count += 1
                    except Exception as e:
                        st.write(
                            f"❌ 「{target['display_name']}」の処理中にエラー: {e}"
                        )
                        failure_count += 1
                    progress_bar.progress((i + 1) / total_targets)
                    time.sleep(0.1)

            status_text.empty()
            progress_bar.empty()
            st.success(
                f"一括処理完了: {success_count}件成功, {failure_count}件失敗/情報なし。"
            )
            load_price_data_cached.clear()
            st.rerun()
    else:
        st.info("一括更新を行うには、まず表示ブランドを選択してください。")

    st.markdown("---")
    if st.session_state.last_active_target_for_update:
        active_target_single = st.session_state.last_active_target_for_update
        btn_label_single = (
            f"「{active_target_single['display_name']}」のデータを取得・更新"
        )
        if st.button(
            btn_label_single, type="primary", key=f"btn_update_active_target_single"
        ):
            with st.spinner(
                f"「{active_target_single['display_name']}」の価格情報をスクレイピング中..."
            ):
                try:
                    prices_single = scrape_prices_for_keyword_and_site(
                        active_target_single["site"],
                        active_target_single["brand_keyword"],
                        max_items_override=SITE_CONFIGS.get(
                            active_target_single["site"], {}
                        ).get("max_items_to_scrape", 30),
                    )
                    if prices_single:
                        save_daily_stats_for_site(
                            active_target_single["site"],
                            active_target_single["brand_keyword"],
                            prices_single,
                        )
                        st.success(
                            f"「{active_target_single['display_name']}」のデータを更新しました。"
                        )
                        load_price_data_cached.clear()
                        st.rerun()
                    else:
                        st.warning(
                            f"「{active_target_single['display_name']}」の価格情報が見つかりませんでした。"
                        )
                except Exception as e:
                    st.error(f"データ取得中にエラーが発生しました: {e}")
    else:
        st.info("ブランドを選択すると個別データ更新ボタンが表示されます。")

    st.markdown("---")
    # --- チャート表示設定 ---
    st.subheader("チャート表示設定")
    ma_short_period = st.number_input(
        "短期移動平均 (日)",
        0,
        30,
        DEFAULT_MOVING_AVERAGE_SHORT,
        1,
        key="ni_ma_short_v3",
    )
    ma_long_period = st.number_input(
        "長期移動平均 (日)", 0, 90, DEFAULT_MOVING_AVERAGE_LONG, 1, key="ni_ma_long_v3"
    )

    show_range_option_multi_brand_v3 = False
    primary_target_for_band_display_name_v3 = None
    if st.session_state.last_active_target_for_update and any(
        t["display_name"] == st.session_state.last_active_target_for_update["display_name"]
        for t in st.session_state.selected_targets_for_chart
    ):
        primary_target_for_band_display_name_v3 = (
            st.session_state.last_active_target_for_update["display_name"]
        )
        show_range_option_multi_brand_v3 = st.checkbox(
            f"「{primary_target_for_band_display_name_v3}」の価格範囲を表示",
            value=False,
            key="cb_show_range_v3",
        )

    st.markdown("---")
    with st.expander("ブランド管理", expanded=False):
        st.subheader("新しいブランドの追加")
        add_sites_list_manage = list(load_brands_cached().keys())
        if not add_sites_list_manage:
            add_sites_list_manage = ["mercari"]
        add_selected_site_manage = st.selectbox(
            "追加先のサイト", add_sites_list_manage, key="add_brand_site_sel_manage_v3"
        )

        site_categories_manage = list(
            load_brands_cached().get(add_selected_site_manage, {"未分類": []}).keys()
        )
        if not site_categories_manage:
            site_categories_manage = ["未分類"]

        add_selected_category_manage = st.selectbox(
            "追加先のカテゴリ (整理用)",
            site_categories_manage,
            key="add_brand_cat_sel_manage_v3",
        )
        new_brand_name_input_manage = st.text_input(
            "追加するブランド名", key="add_brand_name_in_manage_v3"
        )

        if st.button("このブランドを追加", key="add_brand_btn_manage_v3"):
            if (
                add_selected_site_manage
                and add_selected_category_manage
                and new_brand_name_input_manage
            ):
                new_brand_name_to_add = new_brand_name_input_manage.strip()
                if not new_brand_name_to_add:
                    st.warning("ブランド名を入力してください。")
                else:
                    all_brands_data_for_add = load_brands_cached()
                    if add_selected_site_manage not in all_brands_data_for_add:
                        all_brands_data_for_add[add_selected_site_manage] = {}
                    if (
                        add_selected_category_manage
                        not in all_brands_data_for_add[add_selected_site_manage]
                    ):
                        all_brands_data_for_add[add_selected_site_manage][
                            add_selected_category_manage
                        ] = []

                    if (
                        new_brand_name_to_add
                        in all_brands_data_for_add[add_selected_site_manage][
                            add_selected_category_manage
                        ]
                    ):
                        st.warning(
                            f"ブランド「{new_brand_name_to_add}」はサイト「{add_selected_site_manage}」のカテゴリ「{add_selected_category_manage}」に既に存在します。"
                        )
                    else:
                        all_brands_data_for_add[add_selected_site_manage][
                            add_selected_category_manage
                        ].append(new_brand_name_to_add)
                        all_brands_data_for_add[add_selected_site_manage][
                            add_selected_category_manage
                        ].sort()
                        if save_brands_to_json(all_brands_data_for_add):
                            st.success(
                                f"ブランド「{new_brand_name_to_add}」をサイト「{add_selected_site_manage}」のカテゴリ「{add_selected_category_manage}」に追加しました。"
                            )
                            st.rerun()
            else:
                st.caption(f"サイト「{del_selected_site}」にカテゴリはありません。")
        else:
            st.caption("削除できるブランド情報がありません。")

        st.markdown("---")
        st.subheader("既存ブランドの編集/削除")
        edit_sites_list = list(load_brands_cached().keys())
        if not edit_sites_list:
            edit_sites_list = ["---"]
        edit_selected_site = st.selectbox(
            "編集/削除するブランドのサイト",
            edit_sites_list,
            key="edit_brand_site_sel_v3",
            index=0 if "---" not in edit_sites_list else edit_sites_list.index("---"),
        )

        if edit_selected_site != "---" and edit_selected_site in brands_data_all_sites:
            edit_categories_list = list(
                brands_data_all_sites[edit_selected_site].keys()
            )
            if not edit_categories_list:
                edit_categories_list = ["---"]
            edit_selected_category = st.selectbox(
                "カテゴリ",
                edit_categories_list,
                key="edit_brand_cat_sel_v3",
                index=(
                    0
                    if "---" not in edit_categories_list
                    else edit_categories_list.index("---")
                ),
            )

            if (
                edit_selected_category != "---"
                and edit_selected_category in brands_data_all_sites[edit_selected_site]
            ):
                edit_brands_list = list(
                    brands_data_all_sites[edit_selected_site][edit_selected_category]
                )
                if not edit_brands_list:
                    edit_brands_list = ["---"]
                edit_selected_brand = st.selectbox(
                    "ブランド",
                    edit_brands_list,
                    key="edit_brand_name_sel_v3",
                    index=(
                        0
                        if "---" not in edit_brands_list
                        else edit_brands_list.index("---")
                    ),
                )

                if edit_selected_brand != "---":
                    st.markdown(
                        f"**編集/削除対象:** `{edit_selected_site} > {edit_selected_category} > {edit_selected_brand}`"
                    )
                    st.markdown("**ブランド情報の変更:**")
                    all_categories_for_move = list(
                        brands_data_all_sites[edit_selected_site].keys()
                    )
                    new_category_for_move = st.selectbox(
                        "移動先の新しいカテゴリ",
                        all_categories_for_move,
                        index=(
                            all_categories_for_move.index(edit_selected_category)
                            if edit_selected_category in all_categories_for_move
                            else 0
                        ),
                        key="edit_brand_new_cat_sel_v3",
                    )
                    new_brand_name_for_edit = st.text_input(
                        "新しいブランド名 (変更する場合)",
                        value=edit_selected_brand,
                        key="edit_brand_new_name_input_v3",
                    )

                    if st.button("変更を保存", key="save_brand_edit_btn_v3"):
                        new_brand_name_strip = new_brand_name_for_edit.strip()
                        if not new_brand_name_strip:
                            st.warning("新しいブランド名を入力してください。")
                        else:
                            brands_data_to_edit = load_brands_cached()
                            if (
                                edit_selected_brand
                                in brands_data_to_edit[edit_selected_site][
                                    edit_selected_category
                                ]
                            ):
                                brands_data_to_edit[edit_selected_site][
                                    edit_selected_category
                                ].remove(edit_selected_brand)
                            if (
                                new_category_for_move
                                not in brands_data_to_edit[edit_selected_site]
                            ):
                                brands_data_to_edit[edit_selected_site][
                                    new_category_for_move
                                ] = []
                            if (
                                new_brand_name_strip
                                in brands_data_to_edit[edit_selected_site][
                                    new_category_for_move
                                ]
                            ):
                                st.warning(
                                    f"ブランド「{new_brand_name_strip}」はカテゴリ「{new_category_for_move}」に既に存在します。元のブランドを復元します。"
                                )
                                if (
                                    edit_selected_brand
                                    not in brands_data_to_edit[edit_selected_site][
                                        edit_selected_category
                                    ]
                                ):
                                    brands_data_to_edit[edit_selected_site][
                                        edit_selected_category
                                    ].append(edit_selected_brand)
                            else:
                                brands_data_to_edit[edit_selected_site][
                                    new_category_for_move
                                ].append(new_brand_name_strip)
                                brands_data_to_edit[edit_selected_site][
                                    new_category_for_move
                                ].sort()
                                if save_brands_to_json(brands_data_to_edit):
                                    st.success(
                                        f"ブランド「{edit_selected_brand}」を「{new_brand_name_strip}」(カテゴリ: {new_category_for_move}) に変更しました。"
                                    )
                                    st.rerun()

                    st.markdown("**ブランドの削除:**")
                    if st.button(
                        f"「{edit_selected_brand}」を削除する",
                        type="secondary",
                        key="delete_brand_btn_v3",
                    ):
                        if "confirm_delete_brand" not in st.session_state:
                            st.session_state.confirm_delete_brand = False
                        st.session_state.confirm_delete_brand = True
                        st.warning(
                            f"本当に「{edit_selected_site} > {edit_selected_category} > {edit_selected_brand}」を削除しますか？"
                        )

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button(
                                "はい、削除します",
                                type="primary",
                                key="confirm_delete_yes_v3",
                            ):
                                brands_data_to_delete = load_brands_cached()
                                if (
                                    edit_selected_brand
                                    in brands_data_to_delete[edit_selected_site][
                                        edit_selected_category
                                    ]
                                ):
                                    brands_data_to_delete[edit_selected_site][
                                        edit_selected_category
                                    ].remove(edit_selected_brand)
                                    if save_brands_to_json(brands_data_to_delete):
                                        st.success(
                                            f"ブランド「{edit_selected_brand}」を削除しました。"
                                        )
                                        st.session_state.confirm_delete_brand = False
                                        st.rerun()
                                else:
                                    st.error(
                                        "削除対象のブランドが見つかりませんでした。"
                                    )
                                st.session_state.confirm_delete_brand = False
                        with col2:
                            if st.button(
                                "いいえ、キャンセルします", key="confirm_delete_no_v3"
                            ):
                                st.session_state.confirm_delete_brand = False
                                st.info("削除はキャンセルされました。")
                                st.rerun()

if st.session_state.selected_targets_for_chart:
    dataframes_to_plot_dict_main = {}
    any_data_loaded_for_chart_main = False
    for target in st.session_state.selected_targets_for_chart:
        df = load_price_data_cached(target["site"], target["brand_keyword"])
        if not df.empty:
            dataframes_to_plot_main[target_info["display_name"]] = {
                "df": df,
                "site": target["site"],
                "brand_keyword": target["brand_keyword"],
            }
            any_data_loaded = True

    # 最新情報のメトリック表示 (最後にアクティブだったターゲット)
    if st.session_state.last_active_target_for_update:
        active_target_info = st.session_state.last_active_target_for_update
        active_display_name = active_target_info["display_name"]
        if active_display_name in dataframes_to_plot_main:
            df_active = dataframes_to_plot_main[active_display_name]["df"]
            if not df_active.empty:
                st.subheader(f"📊 「{active_display_name}」の最新情報")
                latest_data = df_active.iloc[-1]
                delta_text = "N/A"
                if len(df_active) > 1 and "average_price" in df_active.columns:
                    prev_avg_price = df_active.iloc[-2]["average_price"]
                    curr_avg_price = latest_data["average_price"]
                    if pd.notna(prev_avg_price) and pd.notna(curr_avg_price):
                        delta_value = curr_avg_price - prev_avg_price
                        delta_text = f"{delta_value:,.0f} (前日比)"
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric(
                        label="最新平均価格",
                        value=(f"¥{latest_data['average_price']:,.0f}" if pd.notna(latest_data["average_price"]) else "N/A"),
                        delta=delta_text,
                    )
                with col2:
                    st.metric(
                        label="最新出品数",
                        value=(f"{latest_data['count']:,}" if pd.notna(latest_data["count"]) else "N/A")
                    )
                with col3:
                    st.metric(
                        label="最新日付",
                        value=(latest_data['date'].strftime('%Y-%m-%d') if pd.notna(latest_data['date']) else "N/A")
                    )
                st.markdown("---")


    if any_data_loaded:
        primary_display_name_for_band = None
        if primary_target_for_band_display_obj and show_range_option:
            primary_display_name_for_band = primary_target_for_band_display_obj["display_name"]

        price_chart_main = create_multi_brand_price_trend_chart(
            dataframes_to_plot_main,
            ma_short_period,
            ma_long_period,
            show_price_range_for_primary=show_range_option_multi_brand_v3,
            primary_target_for_band_display=primary_target_for_band_display_name_v3,
        )
        st.plotly_chart(price_chart_main, use_container_width=True)

        with st.expander("選択ブランドの生データ表示 (各最新50件)", expanded=False):
            for display_key, data_dict in dataframes_to_plot_main.items():
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
