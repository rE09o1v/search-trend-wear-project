import os
import csv
import json
import time
import datetime
import random
import re
from pathlib import Path
from statistics import mean

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
import pandas as pd

# === 設定 ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRAND_FILE = BASE_DIR / "brands.json"
PAGE_LOAD_TIMEOUT_SECONDS = 75  # Rakuma SNIDEL のタイムアウト対策として全体的に延長
ELEMENT_WAIT_TIMEOUT_SECONDS = 20  # 要素待機も少し延長

# --- サイト別設定 ---
SITE_CONFIGS = {
    "mercari": {
        "url_template": "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time",
        "item_container_selectors": [
            'li[data-testid="item-cell"]',  # プライマリセレクタ
            'div[data-testid="item-cell"]',  # フォールバック
        ],
        "price_inner_selectors": [
            '[data-testid="price"]',  # プライマリ価格セレクタ
            'span[class*="Price"]',  # 大文字小文字を区別しない Price を含むクラス
            'span[class*="price"]',
        ],
        "max_items_to_scrape": 30,
        "wait_time_after_load": (2, 4),  # ページロード後の追加待機
        "scroll_count": (2, 3),  # (min_scrolls, max_scrolls)
        "scroll_height": (700, 1100),  # スクロール高さ
        "scroll_wait_time": (1.8, 3.0),  # スクロール後の待機
        "headers": {"Accept-Language": "ja-JP,ja;q=0.9"},  # 日本語を最優先に指定
    },
    "rakuma": {
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",
        "item_container_selectors": [".item-box"],
        "price_inner_selectors": [".price", ".item-price__value"],
        "max_items_to_scrape": 25,
        "wait_time_after_load": (2, 4),
        "scroll_count": (2, 3),
        "scroll_height": (500, 700),
        "scroll_wait_time": (1.0, 2.0),
        # "page_load_timeout": 90 # SNIDEL など個別に設定する場合
    },
}

INTER_BRAND_SLEEP_TIME = (4, 8)
INTER_SITE_SLEEP_TIME = (8, 15)

DATA_DIR.mkdir(exist_ok=True)


def setup_driver(site_name=None):
    print(f"{datetime.datetime.now()} WebDriverセットアップ開始... (Site: {site_name})")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    # options.set_capability("goog:loggingPrefs", {'performance': 'ALL', 'browser': 'ALL'})

    driver = None
    try:
        print(
            f"{datetime.datetime.now()} ChromeDriverManager().install() を試行します。"
        )
        service = Service(
            ChromeDriverManager().install()
        )  # RunnerのChromeバージョンに合わせるため自動検出
        print(f"{datetime.datetime.now()} webdriver.Chrome() を試行します。")
        driver = webdriver.Chrome(service=service, options=options)

        # Accept-LanguageヘッダーをCDP経由で設定 (driverインスタンス作成直後)
        if (
            site_name
            and site_name in SITE_CONFIGS
            and "headers" in SITE_CONFIGS[site_name]
        ):
            headers_to_set = SITE_CONFIGS[site_name]["headers"]
            print(
                f"{datetime.datetime.now()} [{site_name}] CDP: Network.setExtraHTTPHeaders にヘッダーを設定: {headers_to_set}"
            )
            try:
                driver.execute_cdp_cmd("Network.enable", {})  # Networkドメインを有効化
                driver.execute_cdp_cmd(
                    "Network.setExtraHTTPHeaders", {"headers": headers_to_set}
                )
                print(
                    f"{datetime.datetime.now()} [{site_name}] CDPヘッダー設定コマンド実行完了。"
                )
            except Exception as e_cdp:
                print(
                    f"{datetime.datetime.now()} ERROR [{site_name}] CDPヘッダー設定失敗: {e_cdp}"
                )

        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        print(
            f"{datetime.datetime.now()} WebDriverのセットアップが完了しました。Driver: {driver}"
        )
        return driver
    except Exception as e:
        print(
            f"{datetime.datetime.now()} ERROR WebDriverセットアップ中にエラー: {type(e).__name__} - {e}"
        )
        if driver:
            driver.quit()
        return None


def extract_price_from_text(text_content, site_name="unknown"):
    if not text_content:
        return None

    # print(f"DEBUG [{site_name}] extract_price_from_text に渡されたテキスト(一部): '{text_content[:100].replace('\n',' ')}'")

    # 日本円表記の優先順位を上げる
    # 1. "¥1,234" や "¥ 1,234"
    price_match_yen_symbol_first = re.search(r"¥\s*([0-9,]+)", text_content)
    if price_match_yen_symbol_first:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_symbol_first.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (¥記号パターン): '{price_match_yen_symbol_first.group(0)}' -> {price_digits}"
            )
            return int(price_digits)

    # 2. "1,234 円"
    price_match_yen_word_last = re.search(r"([0-9,]+)\s*円", text_content)
    if price_match_yen_word_last:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_word_last.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (円表記パターン): '{price_match_yen_word_last.group(0)}' -> {price_digits}"
            )
            return int(price_digits)

    # USドル表記の検出（日本円が取得できなかった場合のフォールバック情報として）
    price_match_usd = re.search(r"US\$\s*([0-9,]+\.?[0-9]*)", text_content)
    if price_match_usd:
        price_str_usd = price_match_usd.group(1).replace(",", "")
        # ログには残すが、日本円ではないためスキップ
        print(
            f"INFO [{site_name}] US$表記の価格を検出: '{price_match_usd.group(0)}' -> {price_str_usd}. 日本円ではないため、この価格は使用しません。"
        )
        return None  # 日本円のみを対象とするためNoneを返す

    # print(f"DEBUG [{site_name}] extract_price: 有効な価格形式が見つかりませんでした - '{text_content[:100].replace('\n',' ')}'")
    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    print(
        f"{datetime.datetime.now()} [{site_name}] スクレイピング開始: {keyword_to_search}"
    )
    if site_name not in SITE_CONFIGS:
        print(
            f"{datetime.datetime.now()} ERROR: サイト '{site_name}' の設定がありません。"
        )
        return []

    config = SITE_CONFIGS[site_name]
    max_items_to_collect = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )
    current_page_load_timeout = config.get(
        "page_load_timeout", PAGE_LOAD_TIMEOUT_SECONDS
    )

    driver = setup_driver(site_name=site_name)
    if not driver:
        print(
            f"{datetime.datetime.now()} [{site_name}] WebDriver起動失敗 '{keyword_to_search}' スキップ。"
        )
        return []

    prices = []
    try:
        url = config["url_template"].format(keyword=keyword_to_search)
        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み試行(最大{current_page_load_timeout}秒): {keyword_to_search} - {url}"
        )
        driver.set_page_load_timeout(current_page_load_timeout)
        driver.get(url)
        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み完了: {keyword_to_search}"
        )

        try:
            page_title = driver.title
            print(
                f"INFO [{site_name}] Page title for '{keyword_to_search}': '{page_title}'"
            )
            if site_name == "mercari" and (
                not page_title or "メルカリ" not in page_title
            ):
                print(
                    f"WARN [{site_name}] Mercariのページタイトルが期待と異なります: '{page_title}'. 캡챠 또는 지역 선택 페이지일 수 있습니다."
                )
                # デバッグ用にスクリーンショットとHTMLソースを保存
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                debug_file_base = (
                    DATA_DIR
                    / f"debug_{site_name}_{re.sub(r'[^a-zA-Z0-9]+', '_', keyword_to_search)}_{timestamp}"
                )
                source_path = debug_file_base.with_suffix(".html")
                screenshot_path = debug_file_base.with_suffix(".png")
                try:
                    with open(source_path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    driver.save_screenshot(str(screenshot_path))
                    print(
                        f"INFO [{site_name}] デバッグ情報保存: {source_path}, {screenshot_path}"
                    )
                except Exception as e_debug:
                    print(f"ERROR [{site_name}] デバッグ情報保存失敗: {e_debug}")

        except Exception as e_title:
            print(f"WARN [{site_name}] ページタイトル取得失敗: {e_title}")

        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 3))))

        items_collected_count = 0
        scroll_count_done = 0
        # scroll_countタプルから最大スクロール回数を取得 (例: (2,3)なら3回)
        min_scrolls, max_scrolls = config.get("scroll_count", (1, 1))

        # スクロールとアイテム取得のループ
        while (
            items_collected_count < max_items_to_collect
            and scroll_count_done < max_scrolls
        ):
            scroll_count_done += 1  # スクロール試行回数を先にインクリメント
            if scroll_count_done > 1:  # 最初の表示以降はスクロール
                scroll_h = random.randint(*config.get("scroll_height", (600, 1000)))
                print(
                    f"{datetime.datetime.now()} [{site_name}] スクロール ({scroll_count_done-1}/{max_scrolls-1}), 高さ: {scroll_h}px..."
                )
                driver.execute_script(f"window.scrollBy(0, {scroll_h});")
                time.sleep(random.uniform(*config.get("scroll_wait_time", (1.5, 2.5))))

            new_items_found_this_pass = False
            for container_selector in config["item_container_selectors"]:
                print(
                    f"{datetime.datetime.now()} [{site_name}] アイテムコンテナ探索: '{container_selector}'"
                )
                try:
                    WebDriverWait(driver, ELEMENT_WAIT_TIMEOUT_SECONDS).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, container_selector)
                        )
                    )
                    item_elements = driver.find_elements(
                        By.CSS_SELECTOR, container_selector
                    )
                    print(
                        f"{datetime.datetime.now()} [{site_name}] セレクタ '{container_selector}' で {len(item_elements)} 件候補検出。"
                    )

                    if (
                        not item_elements
                        and container_selector == config["item_container_selectors"][0]
                    ):
                        print(
                            f"WARN [{site_name}] メインのアイテムセレクタ '{container_selector}' でアイテムが見つかりません。"
                        )

                    for item_el_idx, item_el in enumerate(item_elements):
                        if items_collected_count >= max_items_to_collect:
                            break

                        try:
                            item_text_content = item_el.text
                            price = None
                            price_selector_used = "N/A"
                            price_text_found_in_el = "N/A"

                            for p_selector in config["price_inner_selectors"]:
                                try:
                                    price_elements_in_item = item_el.find_elements(
                                        By.CSS_SELECTOR, p_selector
                                    )
                                    if price_elements_in_item:
                                        price_el = price_elements_in_item[
                                            0
                                        ]  # 最初に見つかったものを使用
                                        price_text_found = price_el.text.strip()
                                        # price_html_for_debug = price_el.get_attribute('outerHTML')
                                        # print(f"DEBUG [{site_name}] Item {item_el_idx}, Price Selector '{p_selector}' found. Text: '{price_text_found}', HTML: '{price_html_for_debug[:100].replace('\n',' ')}'")

                                        if price_text_found:
                                            extracted_p = extract_price_from_text(
                                                price_text_found, site_name
                                            )
                                            if extracted_p is not None:
                                                price = extracted_p
                                                price_selector_used = p_selector
                                                price_text_found_in_el = (
                                                    price_text_found
                                                )
                                                break
                                except NoSuchElementException:  # これは想定内
                                    continue
                                except Exception as e_price_sel:
                                    print(
                                        f"WARN [{site_name}] 価格セレクタ '{p_selector}' 処理中にエラー: {type(e_price_sel).__name__}"
                                    )
                                    break

                            if price is None and item_text_content:  # フォールバック
                                extracted_p_fallback = extract_price_from_text(
                                    item_text_content, site_name
                                )
                                if extracted_p_fallback is not None:
                                    price = extracted_p_fallback
                                    price_selector_used = "item_el.text (fallback)"
                                    price_text_found_in_el = item_text_content[:30]

                            if price is not None:
                                prices.append(price)
                                items_collected_count += 1
                                new_items_found_this_pass = True
                                print(
                                    f"INFO [{site_name}] 価格取得成功 ({items_collected_count}/{max_items_to_collect}): {price} (from '{price_selector_used}', text: '{price_text_found_in_el.strip().replace('\n',' ')}')"
                                )
                            # else:
                            # print(f"DEBUG [{site_name}] 価格抽出失敗 (Item {item_el_idx}). Text: '{item_text_content[:100].replace('\n', ' ')}...'")

                        except StaleElementReferenceException:
                            print(
                                f"{datetime.datetime.now()} WARN [{site_name}] アイテム処理中にStaleElement。このアイテムをスキップ。"
                            )
                            continue
                        except Exception as e_item_proc:
                            print(
                                f"{datetime.datetime.now()} ERROR [{site_name}] アイテム個別処理中: {type(e_item_proc).__name__} - {e_item_proc}"
                            )

                        if items_collected_count >= max_items_to_collect:
                            break
                        time.sleep(random.uniform(0.02, 0.08))

                    if items_collected_count >= max_items_to_collect:
                        break

                except TimeoutException:
                    print(
                        f"{datetime.datetime.now()} INFO [{site_name}] コンテナセレクタ '{container_selector}' で要素待機タイムアウト。"
                    )
                    continue  # 次のコンテナセレクタへ
                except Exception as e_container_loop:
                    print(
                        f"{datetime.datetime.now()} ERROR [{site_name}] アイテムコンテナ処理中: {e_container_loop}"
                    )

            if items_collected_count >= max_items_to_collect:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 目標取得数 {max_items_to_collect} 件に到達。"
                )
                break
            if not new_items_found_this_pass and scroll_count_done >= max_scrolls:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 今回のスクロールで新アイテム見つからず、かつ最大スクロール回数 ({max_scrolls}) 到達。"
                )
                break
            elif not new_items_found_this_pass:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 今回のスクロールで新アイテム見つからず。次のスクロール試行 ({scroll_count_done}/{max_scrolls})。"
                )

        if not prices:
            print(
                f"{datetime.datetime.now()} WARN [{site_name}] 価格データ最終的になし (0件): {keyword_to_search}"
            )

    except TimeoutException as e_page_load:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] ページ読込タイムアウト({current_page_load_timeout}秒): {keyword_to_search} - {getattr(e_page_load, 'msg', str(e_page_load))}"
        )
    except WebDriverException as e_wd_main:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] WebDriver操作中: {keyword_to_search} - {type(e_wd_main).__name__}: {getattr(e_wd_main, 'msg', str(e_wd_main))}"
        )
    except Exception as e_main:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] スクレイピング全体で予期せぬエラー: {keyword_to_search} - {type(e_main).__name__}: {e_main}"
        )
    finally:
        if driver:
            try:
                driver.quit()
                print(
                    f"{datetime.datetime.now()} [{site_name}] WebDriver終了: {keyword_to_search}"
                )
            except Exception as e_quit:
                print(
                    f"{datetime.datetime.now()} ERROR [{site_name}] WebDriver終了時: {e_quit}"
                )

    print(
        f"{datetime.datetime.now()} [{site_name}] キーワード '{keyword_to_search}' で {len(prices)} 件の価格を取得完了。"
    )
    return prices


# (save_daily_stats_for_site, load_brands_from_json, main_scrape_all は変更なしのため省略)
# ... (前のCanvasのコードの残りの部分をここにコピーしてください) ...


def save_daily_stats_for_site(site_name, brand_keyword, prices):
    if not prices:
        print(
            f"{datetime.datetime.now()} INFO [{site_name}] 保存する価格データなし: {brand_keyword}"
        )
        return

    today_str = datetime.date.today().isoformat()
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = f"{safe_site_name}_{safe_brand_keyword}.csv"
    file_path = DATA_DIR / file_name

    count = len(prices)
    average_price = mean(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    new_data_row = {
        "date": today_str,
        "site": site_name,
        "keyword": brand_keyword,
        "count": count,
        "average_price": round(average_price, 2),
        "min_price": min_price,
        "max_price": max_price,
    }

    df_existing = pd.DataFrame(columns=list(new_data_row.keys()))
    try:
        if file_path.exists() and os.path.getsize(file_path) > 0:
            try:
                df_existing = pd.read_csv(file_path, dtype={"date": str})
                # Ensure 'date' column exists and is not all NaT before formatting
                if (
                    "date" in df_existing.columns
                    and not df_existing["date"].isnull().all()
                ):
                    df_existing["date"] = pd.to_datetime(
                        df_existing["date"], errors="coerce"
                    ).dt.strftime("%Y-%m-%d")
                    df_existing = df_existing.dropna(
                        subset=["date"]
                    )  # Drop rows where date conversion failed
                else:  # date列がない、または全てNaTの場合は、新しいデータで上書きするための準備
                    df_existing = pd.DataFrame(columns=list(new_data_row.keys()))

            except Exception as e_read:
                print(
                    f"{datetime.datetime.now()} WARN: {file_path} 読込失敗: {e_read}。新規作成扱い。"
                )
                df_existing = pd.DataFrame(
                    columns=list(new_data_row.keys())
                )  # エラー時も空のDFで初期化

        mask = pd.Series(False, index=df_existing.index)
        if (
            not df_existing.empty
            and "date" in df_existing.columns
            and not df_existing["date"].isnull().all()
        ):
            mask = (
                (df_existing["date"] == today_str)
                & (df_existing["site"] == site_name)
                & (df_existing["keyword"] == brand_keyword)
            )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            update_cols = ["count", "average_price", "min_price", "max_price"]
            for col_name in update_cols:  # Iterate directly
                df_existing.loc[existing_today_data_indices, col_name] = new_data_row[
                    col_name
                ]
            print(
                f"{datetime.datetime.now()} INFO [{site_name}] '{brand_keyword}' 本日データ更新: {file_name}"
            )
        else:
            new_df_row_df = pd.DataFrame([new_data_row])
            if (
                df_existing.empty
            ):  # Handle case where df_existing might only have columns but no rows
                df_existing = new_df_row_df
            else:
                df_existing = pd.concat([df_existing, new_df_row_df], ignore_index=True)
            print(
                f"{datetime.datetime.now()} INFO [{site_name}] '{brand_keyword}' 新規価格統計保存: {file_name}"
            )

        if "date" in df_existing.columns and not df_existing.empty:
            df_existing = df_existing.sort_values(by="date", ascending=False)
            df_existing = df_existing.drop_duplicates(
                subset=["site", "keyword", "date"], keep="first"
            )
            df_existing = df_existing.sort_values(by="date", ascending=True)

        df_existing.to_csv(file_path, index=False, encoding="utf-8")
    except Exception as e:
        print(
            f"{datetime.datetime.now()} ERROR データ保存中 ({file_path}): {type(e).__name__} - {e}"
        )


def load_brands_from_json():
    if not BRAND_FILE.exists():
        print(f"{datetime.datetime.now()} ERROR: {BRAND_FILE} が見つかりません。")
        return {}
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            brands_data = json.load(f)
        print(f"{datetime.datetime.now()} INFO: {BRAND_FILE} を正常に読み込みました。")
        return brands_data
    except Exception as e:
        print(f"{datetime.datetime.now()} ERROR: {BRAND_FILE} 読込中: {e}")
        return {}


def main_scrape_all():
    overall_start_time = datetime.datetime.now()
    print(f"{overall_start_time} 一括スクレイピング処理を開始します...")
    brands_data_all_sites = load_brands_from_json()

    if not brands_data_all_sites:
        print(
            f"{datetime.datetime.now()} ERROR: ブランド情報が読み込めなかったため、処理を終了します。"
        )
        return

    total_sites_count = len(brands_data_all_sites)
    for site_idx, (site_name, site_brands_data) in enumerate(
        brands_data_all_sites.items()
    ):
        site_process_start_time = datetime.datetime.now()
        print(
            f"\n{site_process_start_time} --- サイト処理開始 ({site_idx+1}/{total_sites_count}): {site_name} ---"
        )

        if site_name not in SITE_CONFIGS:
            print(
                f"{datetime.datetime.now()} WARN: サイト '{site_name}' の設定がSITE_CONFIGSに存在しません。スキップします。"
            )
            continue

        for category_name, brands_in_category in site_brands_data.items():
            print(
                f"{datetime.datetime.now()}   -- カテゴリ処理中: {category_name} ({len(brands_in_category)}ブランド) --"
            )
            for brand_idx_in_cat, brand_keyword in enumerate(brands_in_category):
                brand_loop_start_time = datetime.datetime.now()
                print(
                    f"{brand_loop_start_time}     - ブランド ({brand_idx_in_cat+1}/{len(brands_in_category)}): {brand_keyword} ({site_name})"
                )

                prices = scrape_prices_for_keyword_and_site(site_name, brand_keyword)

                if prices:
                    save_daily_stats_for_site(site_name, brand_keyword, prices)
                else:
                    print(
                        f"{datetime.datetime.now()} INFO [{site_name}] ブランド '{brand_keyword}' の有効な価格情報が見つからなかったため、CSVファイルは更新/作成されません。"
                    )

                brand_loop_end_time = datetime.datetime.now()
                print(
                    f"{brand_loop_end_time}     - ブランド '{brand_keyword}' 処理完了。所要時間: {brand_loop_end_time - brand_loop_start_time}"
                )

                if brand_idx_in_cat < len(brands_in_category) - 1:
                    sleep_duration = random.uniform(*INTER_BRAND_SLEEP_TIME)
                    print(
                        f"{datetime.datetime.now()}     - 次のブランドまで {sleep_duration:.1f} 秒待機..."
                    )
                    time.sleep(sleep_duration)
            print(
                f"{datetime.datetime.now()}   -- カテゴリ '{category_name}' 処理完了 --"
            )

        site_process_end_time = datetime.datetime.now()
        print(
            f"{site_process_end_time} --- サイト '{site_name}' 処理完了。所要時間: {site_process_end_time - site_process_start_time} ---"
        )

        if site_idx < total_sites_count - 1:  # 最後のサイト処理後でなければスリープ
            site_sleep_duration = random.uniform(*INTER_SITE_SLEEP_TIME)
            print(
                f"{datetime.datetime.now()} 次のサイト処理まで {site_sleep_duration:.1f} 秒待機..."
            )
            time.sleep(site_sleep_duration)

    overall_end_time = datetime.datetime.now()
    print(
        f"\n{overall_end_time} 全ての一括スクレイピング処理が完了しました。総所要時間: {overall_end_time - overall_start_time}"
    )


if __name__ == "__main__":
    main_scrape_all()
