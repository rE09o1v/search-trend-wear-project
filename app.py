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


APP_TITLE = "価格動向トラッカー (ブランド名検索)"
BRAND_FILE = Path(__file__).resolve().parent / "brands.json" # app.py と同じ階層に brands.json
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
                "ストリート": ["Supreme", "Stussy", "A BATHING APE"],
                "モード系": ["Yohji Yamamoto", "COMME des GARCONS", "ISSEY MIYAKE"],
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


@st.cache_data(ttl=600) # 10分キャッシュ
def load_price_data_cached(site_name, brand_keyword):
    """指定されたサイトとブランドの価格データをCSVから読み込み、キャッシュする。"""
    # ファイル名の衝突を避けるため、ブランドキーワードとサイト名を安全な形に変換
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    
    # scraper.py と同じファイル命名規則を使用
    file_name = f"{safe_site_name}_{safe_brand_keyword}.csv"
    file_path = DATA_DIR / file_name # scraper.py からインポートした DATA_DIR を使用

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
            # CSVには特定のブランドのデータのみが含まれているはずなので、追加のフィルタリングは不要
            return df
        except pd.errors.EmptyDataError:
            st.warning(f"{file_name} は空です。")
            return pd.DataFrame(columns=EXPECTED_COLUMNS_BASE)
        except Exception as e:
            st.error(f"{file_name} の読み込み中にエラーが発生しました: {e}")
            return pd.DataFrame(columns=EXPECTED_COLUMNS_BASE)
    return pd.DataFrame(columns=EXPECTED_COLUMNS_BASE)


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
        brand_name = df_data["brand_keyword"] # brands.json からのブランド名

        if df.empty or "average_price" not in df.columns or df["average_price"].isnull().all():
            continue

        current_color = PLOTLY_COLORS[color_idx % len(PLOTLY_COLORS)]
        legend_name_prefix = f"{site_name}: {brand_name}" # 凡例にはサイト名とブランド名

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
    st.session_state.selected_targets_for_chart = [] # {'site': str, 'brand_keyword': str, 'display_name': str, 'category_for_json': str}
if "last_active_target_for_update" not in st.session_state: # データ更新ボタンの対象
    st.session_state.last_active_target_for_update = None

# --- サイドバー ---
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

    # 選択されたサイトのブランド情報を表示
    current_brands_on_selected_site = brands_data_all_sites.get(selected_site_for_sidebar_display, {})
    
    # チェックボックスの状態を管理するための一時リスト
    temp_selected_targets_from_checkboxes = list(st.session_state.selected_targets_for_chart)

    if not current_brands_on_selected_site:
        st.info(f"「{selected_site_for_sidebar_display}」にはまだブランドが登録されていません。下の「ブランド管理」から追加してください。")
    else:
        for category, brands_in_cat in current_brands_on_selected_site.items():
            # カテゴリが空でもExpanderを表示（未分類など）
            with st.expander(f"{category} ({len(brands_in_cat) if brands_in_cat else 0})", expanded=True):
                if not brands_in_cat:
                    st.caption("このカテゴリにブランドはありません。")
                else:
                    for brand_name_from_json in sorted(brands_in_cat): # ブランド名をソートして表示
                        target_obj = {
                            "site": selected_site_for_sidebar_display,
                            "brand_keyword": brand_name_from_json, # 検索・保存用
                            "display_name": f"{selected_site_for_sidebar_display}: {brand_name_from_json}", # チャート凡例等
                            "category_for_json": category, # brands.json操作用
                        }
                        # チェックボックスのキーはユニークにする
                        checkbox_key = f"cb_target_{target_obj['display_name'].replace(' ', '_').replace(':', '_')}"
                        
                        # セッションステートにキーが存在しない場合、初期値を設定
                        # (既に選択されているものはTrueになるように)
                        is_already_selected = any(t["display_name"] == target_obj["display_name"] for t in st.session_state.selected_targets_for_chart)
                        if checkbox_key not in st.session_state:
                             st.session_state[checkbox_key] = is_already_selected

                        is_checked = st.checkbox(
                            brand_name_from_json, # 表示はブランド名のみ
                            key=checkbox_key,
                            # on_change コールバックは複雑になるため、ループ後に一括処理
                        )

                        # チェックボックスの状態に基づいて一時リストを更新
                        if is_checked:
                            if not any(t["display_name"] == target_obj["display_name"] for t in temp_selected_targets_from_checkboxes):
                                temp_selected_targets_from_checkboxes.append(target_obj)
                            # チェックされたものを「最後にアクティブだったもの」として更新
                            st.session_state.last_active_target_for_update = target_obj
                        else:
                            temp_selected_targets_from_checkboxes = [
                                t for t in temp_selected_targets_from_checkboxes if t["display_name"] != target_obj["display_name"]
                            ]
    
    # チェックボックスの変更をセッションステートに反映
    st.session_state.selected_targets_for_chart = temp_selected_targets_from_checkboxes


    # --- チャート表示対象の確認 ---
    if st.session_state.selected_targets_for_chart:
        st.markdown(f"**チャート表示対象 ({len(st.session_state.selected_targets_for_chart)}件):**")
        for t in st.session_state.selected_targets_for_chart[:5]: # 最大5件表示
            st.markdown(f"- `{t['display_name']}`")
        if len(st.session_state.selected_targets_for_chart) > 5:
            st.markdown("  ...")
    else:
        st.markdown("チャートに表示するブランドを選択してください。")

    st.markdown("---")

    # --- データ取得ボタン ---
    if st.session_state.last_active_target_for_update:
        active_target = st.session_state.last_active_target_for_update
        btn_label = f"「{active_target['display_name']}」のデータ取得/更新"
        
        # SITE_CONFIGS にサイト設定があるか確認
        if active_target['site'] not in SITE_CONFIGS:
            st.warning(f"サイト「{active_target['site']}」のスクレイピング設定が scraper.py の SITE_CONFIGS に見つかりません。")
        elif st.button(btn_label, type="primary", key=f"btn_update_active_target_{active_target['site']}_{active_target['brand_keyword']}"):
            with st.spinner(f"「{active_target['display_name']}」の価格情報をスクレイピング中... (時間がかかる場合があります)"):
                try:
                    # scraper.py の関数を呼び出し
                    prices = scrape_prices_for_keyword_and_site(
                        active_target["site"],
                        active_target["brand_keyword"], # ブランド名のみを渡す
                        max_items_override=SITE_CONFIGS.get(active_target["site"], {}).get("max_items_to_scrape", 30)
                    )
                    if prices:
                        save_daily_stats_for_site(
                            active_target["site"],
                            active_target["brand_keyword"], # ブランド名のみを渡す
                            prices,
                        )
                        st.success(f"「{active_target['display_name']}」のデータを更新しました。({len(prices)}件の価格取得)")
                        load_price_data_cached.clear() # キャッシュクリア
                        st.rerun() # UIを再描画して最新データを反映
                    else:
                        st.warning(f"「{active_target['display_name']}」の価格情報が見つかりませんでした。サイト上で検索結果がないか、セレクタが変更された可能性があります。")
                except Exception as e_scrape:
                    st.error(f"データ取得中にエラーが発生しました: {e_scrape}")
                    st.exception(e_scrape) # 詳細なエラー情報を表示
    else:
        st.info("ブランドを選択すると、そのブランドのデータ更新ボタンが表示されます。")

    st.markdown("---")
    # --- チャート表示設定 ---
    st.subheader("チャート表示設定")
    ma_short_period = st.number_input("短期移動平均 (日)", 0, 30, DEFAULT_MOVING_AVERAGE_SHORT, 1, key="ni_ma_short")
    ma_long_period = st.number_input("長期移動平均 (日)", 0, 90, DEFAULT_MOVING_AVERAGE_LONG, 1, key="ni_ma_long")

    # 価格範囲表示のチェックボックス
    # 最後にアクティブだったターゲットがチャート表示対象に含まれている場合のみ表示
    primary_target_for_band_display_obj = None
    if st.session_state.last_active_target_for_update and any(
        t["display_name"] == st.session_state.last_active_target_for_update["display_name"]
        for t in st.session_state.selected_targets_for_chart
    ):
        primary_target_for_band_display_obj = st.session_state.last_active_target_for_update
    
    show_range_option = False
    if primary_target_for_band_display_obj:
        show_range_option = st.checkbox(
            f"「{primary_target_for_band_display_obj['display_name']}」の価格範囲を表示",
            value=False, # デフォルトはオフ
            key="cb_show_range"
        )

    st.markdown("---")
    # --- ブランド管理 ---
    with st.expander("ブランド管理 (追加/削除)", expanded=False):
        st.subheader("新しいブランドの追加")
        # 追加先サイトの選択 (brands.jsonに存在するサイト + SITE_CONFIGSに存在するサイト)
        add_sites_available = sorted(list(set(available_sites_from_brands + list(SITE_CONFIGS.keys()))))
        if not add_sites_available:
             add_sites_available = ["mercari", "rakuma"] # フォールバック

        add_selected_site = st.selectbox("追加先のサイト", add_sites_available, key="add_brand_site_sel")

        # 追加先カテゴリの選択 (既存カテゴリ + 新規入力)
        existing_categories_on_add_site = list(brands_data_all_sites.get(add_selected_site, {"未分類": []}).keys())
        category_options = sorted(list(set(existing_categories_on_add_site + ["新しいカテゴリを作成"])))
        
        add_selected_category_choice = st.selectbox(
            "追加先のカテゴリ", category_options, key="add_brand_cat_sel"
        )
        
        add_new_category_name = ""
        if add_selected_category_choice == "新しいカテゴリを作成":
            add_new_category_name = st.text_input("新しいカテゴリ名を入力", key="add_brand_new_cat_name").strip()
            final_category_to_add = add_new_category_name if add_new_category_name else "未分類"
        else:
            final_category_to_add = add_selected_category_choice

        new_brand_name_to_add = st.text_input("追加するブランド名", key="add_brand_name_in").strip()

        if st.button("このブランドを追加", key="add_brand_btn"):
            if add_selected_site and final_category_to_add and new_brand_name_to_add:
                all_brands_data = load_brands_cached() # 最新のデータを取得
                
                # サイトが存在しない場合は作成
                if add_selected_site not in all_brands_data:
                    all_brands_data[add_selected_site] = {}
                # カテゴリが存在しない場合は作成
                if final_category_to_add not in all_brands_data[add_selected_site]:
                    all_brands_data[add_selected_site][final_category_to_add] = []

                # ブランドが既に存在しないか確認
                if new_brand_name_to_add not in all_brands_data[add_selected_site][final_category_to_add]:
                    all_brands_data[add_selected_site][final_category_to_add].append(new_brand_name_to_add)
                    all_brands_data[add_selected_site][final_category_to_add].sort() # カテゴリ内のブランドをソート
                    if save_brands_to_json(all_brands_data):
                        st.success(f"ブランド「{new_brand_name_to_add}」をサイト「{add_selected_site}」のカテゴリ「{final_category_to_add}」に追加しました。")
                        st.rerun()
                else:
                    st.warning(f"ブランド「{new_brand_name_to_add}」は既にカテゴリ「{final_category_to_add}」に存在します。")
            else:
                st.warning("サイト、カテゴリ、ブランド名をすべて入力してください。")
        
        st.subheader("ブランドの削除")
        del_sites_available = list(brands_data_all_sites.keys())
        if del_sites_available:
            del_selected_site = st.selectbox("削除対象のサイト", del_sites_available, key="del_brand_site_sel")
            
            categories_on_del_site = list(brands_data_all_sites.get(del_selected_site, {}).keys())
            if categories_on_del_site:
                del_selected_category = st.selectbox("削除対象のカテゴリ", categories_on_del_site, key="del_brand_cat_sel")
                
                brands_in_del_category = brands_data_all_sites.get(del_selected_site, {}).get(del_selected_category, [])
                if brands_in_del_category:
                    brand_to_delete = st.selectbox("削除するブランドを選択", sorted(brands_in_del_category), key="del_brand_name_sel")
                    if st.button(f"「{brand_to_delete}」を削除", type="secondary", key="del_brand_btn"):
                        all_brands_data_for_del = load_brands_cached()
                        if brand_to_delete in all_brands_data_for_del.get(del_selected_site, {}).get(del_selected_category, []):
                            all_brands_data_for_del[del_selected_site][del_selected_category].remove(brand_to_delete)
                            # ブランドリストが空になったらカテゴリ自体を削除するオプション
                            if not all_brands_data_for_del[del_selected_site][del_selected_category] and del_selected_category != "未分類":
                                del all_brands_data_for_del[del_selected_site][del_selected_category]
                            # サイトにカテゴリがなくなったらサイト自体を削除するオプション (未分類のみの場合は残す)
                            if not all_brands_data_for_del[del_selected_site] and len(all_brands_data_for_del[del_selected_site].get("未分類", [])) == 0 :
                                if not any(cat_list for cat_name, cat_list in all_brands_data_for_del[del_selected_site].items() if cat_name != "未分類" or cat_list):
                                     del all_brands_data_for_del[del_selected_site]


                            if save_brands_to_json(all_brands_data_for_del):
                                st.success(f"ブランド「{brand_to_delete}」を削除しました。")
                                # 関連するCSVファイルも削除するかユーザーに尋ねる (オプション)
                                # safe_brand_del = re.sub(r'[\\/*?:"<>|]', "_", brand_to_delete)
                                # safe_site_del = re.sub(r'[\\/*?:"<>|]', "_", del_selected_site)
                                # csv_to_del = DATA_DIR / f"{safe_site_del}_{safe_brand_del}.csv"
                                # if csv_to_del.exists():
                                # if st.checkbox(f"{csv_to_del.name} も削除しますか？"):
                                # os.remove(csv_to_del)
                                # st.info(f"{csv_to_del.name} を削除しました。")
                                st.rerun()
                        else:
                            st.error("削除対象のブランドが見つかりませんでした。ページを再読み込みしてください。")
                else:
                    st.caption(f"カテゴリ「{del_selected_category}」に削除できるブランドはありません。")
            else:
                st.caption(f"サイト「{del_selected_site}」にカテゴリはありません。")
        else:
            st.caption("削除できるブランド情報がありません。")


# --- メインコンテンツエリア ---
if st.session_state.selected_targets_for_chart:
    dataframes_to_plot_main = {}
    any_data_loaded = False
    
    # 選択されたターゲットのデータをロード
    for target_info in st.session_state.selected_targets_for_chart:
        df = load_price_data_cached(target_info["site"], target_info["brand_keyword"])
        if not df.empty:
            dataframes_to_plot_main[target_info["display_name"]] = {
                "df": df,
                "site": target_info["site"],
                "brand_keyword": target_info["brand_keyword"],
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
            show_price_range_for_primary=show_range_option, # チェックボックスの状態を渡す
            primary_target_for_band_display=primary_display_name_for_band # 価格範囲を表示する対象のdisplay_name
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
