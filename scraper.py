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
PAGE_LOAD_TIMEOUT_SECONDS = 60  # Rakumaのタイムアウト対策として少し延長
ELEMENT_WAIT_TIMEOUT_SECONDS = 15

# --- サイト別設定 ---
SITE_CONFIGS = {
    "mercari": {
        "url_template": "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time",
        "item_container_selectors": [
            'li[data-testid="item-cell"]',
            'div[data-testid="item-cell"]',
        ],
        "price_inner_selectors": [
            '[data-testid="price"]',
            'span[class*="ItemPrice"]',  # Mercariの価格表示に使われる可能性のあるクラス
            'span[class*="price"]',  # 一般的な価格表示
        ],
        "max_items_to_scrape": 30,
        "wait_time_after_load": (2, 3),
        "scroll_count": (2, 3),
        "scroll_height": (600, 1000),
        "scroll_wait_time": (1.5, 2.5),
        "headers": {  # Mercari用に言語設定を試みる
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
        },
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
        # Rakumaのタイムアウトが特定ブランドで頻発する場合、ここにも個別タイムアウト設定を検討
        # "page_load_timeout": 90 # 例: SNIDEL用
    },
}

INTER_BRAND_SLEEP_TIME = (3, 7)
INTER_SITE_SLEEP_TIME = (5, 10)

DATA_DIR.mkdir(exist_ok=True)


def setup_driver(site_name=None):  # サイト名を渡せるように変更
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

    # サイト固有のヘッダー設定を適用 (Accept-Languageなど)
    if site_name and site_name in SITE_CONFIGS and "headers" in SITE_CONFIGS[site_name]:
        for key, value in SITE_CONFIGS[site_name]["headers"].items():
            print(
                f"{datetime.datetime.now()} Setting header for {site_name}: {key}={value}"
            )
            options.add_argument(
                f"--header={key}: {value}"
            )  # ヘッダー設定方法の確認が必要。これは一般的な引数ではない。
            # Selenium 4では options.set_capability や execute_cdp_cmd を使う

    # User-Agentは固定でも良いが、より動的にすることも可能
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    # options.set_capability("goog:loggingPrefs", {'performance': 'ALL', 'browser': 'ALL'}) # 詳細ログが必要な場合

    driver = None
    try:
        print(
            f"{datetime.datetime.now()} ChromeDriverManager().install() を試行します。"
        )
        service = Service(ChromeDriverManager().install())
        print(f"{datetime.datetime.now()} webdriver.Chrome() を試行します。")

        # ヘッダーをより確実に設定する方法 (Selenium 4+)
        # これは options.add_argument("--header=...") より推奨される
        # ただし、この capabilities は ChromeOptions() に直接渡すのではなく、
        # webdriver.Chrome の capabilities パラメータに渡すか、 options.capabilities にマージする必要がある
        # options.set_capability("goog:chromeOptions", {"args": [], "prefs": {}, "mobileEmulation": {}}) # 初期化

        # if site_name and site_name in SITE_CONFIGS and "headers" in SITE_CONFIGS[site_name]:
        #     # ここで execute_cdp_cmd を使う方法もある
        #     # driver.execute_cdp_cmd('Network.setExtraHTTPHeaders', {'headers': SITE_CONFIGS[site_name]["headers"]})
        #     # ただし、driverインスタンス作成後でないと使えない
        #     pass

        driver = webdriver.Chrome(service=service, options=options)

        # driverインスタンス作成後にヘッダーを設定 (Mercariの場合)
        if site_name == "mercari" and "headers" in SITE_CONFIGS["mercari"]:
            print(
                f"{datetime.datetime.now()} Executing CDP command to set headers for Mercari."
            )
            driver.execute_cdp_cmd(
                "Network.setExtraHTTPHeaders",
                {"headers": SITE_CONFIGS["mercari"]["headers"]},
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
            f"{datetime.datetime.now()} WebDriverセットアップ中にエラー: {type(e).__name__} - {e}"
        )
        if driver:
            driver.quit()
        return None


def extract_price_from_text(text_content, site_name="unknown"):
    if not text_content:
        return None

    # print(f"DEBUG [{site_name}] extract_price_from_text に渡されたテキスト: '{text_content[:200].replace('\n',' ')}'") # 長すぎるので一部表示

    price_match_yen_symbol_first = re.search(r"¥\s*([0-9,]+)", text_content)
    if price_match_yen_symbol_first:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_symbol_first.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (¥先頭): '{price_match_yen_symbol_first.group(0)}' -> {price_digits}"
            )
            return int(price_digits)

    price_match_yen_word_last = re.search(r"([0-9,]+)\s*円", text_content)
    if price_match_yen_word_last:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_word_last.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (円末尾): '{price_match_yen_word_last.group(0)}' -> {price_digits}"
            )
            return int(price_digits)

    price_match_usd = re.search(r"US\$\s*([0-9,]+\.?[0-9]*)", text_content)
    if price_match_usd:
        price_str_usd = price_match_usd.group(1).replace(",", "")
        print(
            f"INFO [{site_name}] US$表記の価格を検出: '{price_match_usd.group(0)}' -> {price_str_usd}. Mercariでは日本円表示を期待しているためスキップ。"
        )
        return None

    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    print(
        f"{datetime.datetime.now()} [{site_name}] スクレイピング開始: {keyword_to_search}"
    )
    if site_name not in SITE_CONFIGS:
        print(
            f"{datetime.datetime.now()} ERROR: サイト '{site_name}' の設定が見つかりません。"
        )
        return []

    config = SITE_CONFIGS[site_name]
    max_items_to_collect = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )

    # サイト固有のページロードタイムアウトがあれば使用
    current_page_load_timeout = config.get(
        "page_load_timeout", PAGE_LOAD_TIMEOUT_SECONDS
    )

    driver = setup_driver(site_name=site_name)  # サイト名を渡す
    if not driver:
        print(
            f"{datetime.datetime.now()} [{site_name}] WebDriver起動失敗のため '{keyword_to_search}' をスキップ。"
        )
        return []

    prices = []
    try:
        url = config["url_template"].format(keyword=keyword_to_search)
        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み試行 (最大{current_page_load_timeout}秒): {keyword_to_search} - {url}"
        )
        driver.set_page_load_timeout(current_page_load_timeout)

        driver.get(
            url
        )  # ここで Network.setExtraHTTPHeaders が適用される (Mercariの場合)

        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み完了: {keyword_to_search}"
        )

        # ページタイトルや一部内容をログに出力して、期待通りのページか確認
        try:
            page_title = driver.title
            print(f"INFO [{site_name}] Page title: {page_title}")
            # body_snippet = driver.find_element(By.TAG_NAME, "body").text[:200].replace("\n", " ")
            # print(f"INFO [{site_name}] Body snippet: {body_snippet}")
        except Exception as e_page_check:
            print(f"WARN [{site_name}] Page title/snippet check failed: {e_page_check}")

        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 4))))

        items_collected_count = 0
        scroll_count_done = 0
        max_scrolls = config.get("scroll_count", (1, 1))[1]

        while (
            items_collected_count < max_items_to_collect
            and scroll_count_done <= max_scrolls
        ):
            if scroll_count_done > 0:
                scroll_h = random.randint(*config.get("scroll_height", (500, 900)))
                print(
                    f"{datetime.datetime.now()} [{site_name}] スクロール ({scroll_count_done}/{max_scrolls}), 高さ: {scroll_h}px..."
                )
                driver.execute_script(f"window.scrollBy(0, {scroll_h});")
                time.sleep(random.uniform(*config.get("scroll_wait_time", (1.5, 2.5))))

            scroll_count_done += 1
            new_items_found_this_scroll = False

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
                            f"WARN [{site_name}] メインセレクタ '{container_selector}' でアイテムが見つかりません。ページ構造確認要。"
                        )

                    for item_el_idx, item_el in enumerate(item_elements):
                        if items_collected_count >= max_items_to_collect:
                            break

                        try:
                            item_text_content = item_el.text
                            price = None
                            price_selector_used = "N/A"
                            price_text_found_detail = "N/A"

                            for p_selector_idx, p_selector in enumerate(
                                config["price_inner_selectors"]
                            ):
                                try:
                                    price_el = item_el.find_element(
                                        By.CSS_SELECTOR, p_selector
                                    )
                                    price_text_found = price_el.text.strip()
                                    price_html_for_debug = price_el.get_attribute(
                                        "outerHTML"
                                    )  # デバッグ用にHTML取得

                                    if price_text_found:
                                        print(
                                            f"DEBUG [{site_name}] Item {item_el_idx}, Price Selector '{p_selector}' found. Text: '{price_text_found}', HTML: '{price_html_for_debug[:100].replace('\n',' ')}'"
                                        )
                                        extracted_p = extract_price_from_text(
                                            price_text_found, site_name
                                        )
                                        if extracted_p is not None:
                                            price = extracted_p
                                            price_selector_used = p_selector
                                            price_text_found_detail = price_text_found
                                            break
                                # except NoSuchElementException: # このセレクタでは見つからなかった (よくあること)
                                #     # print(f"DEBUG [{site_name}] Price selector '{p_selector}' not found in item {item_el_idx}")
                                #     continue
                                except (
                                    Exception
                                ) as e_price_find:  # StaleElementやその他のエラー
                                    print(
                                        f"WARN [{site_name}] Price selector '{p_selector}' でエラー: {type(e_price_find).__name__}"
                                    )
                                    break  # このアイテムの価格セレクタ探索は中断

                            if price is None and item_text_content:
                                # print(f"DEBUG [{site_name}] Item {item_el_idx} - フォールバックで item_el.text から価格抽出試行: '{item_text_content[:100]}'")
                                extracted_p_fallback = extract_price_from_text(
                                    item_text_content, site_name
                                )
                                if extracted_p_fallback is not None:
                                    price = extracted_p_fallback
                                    price_selector_used = "item_el.text (fallback)"
                                    price_text_found_detail = item_text_content[
                                        :30
                                    ]  # 最初の30文字

                            if price is not None:
                                prices.append(price)
                                items_collected_count += 1
                                new_items_found_this_scroll = True
                                print(
                                    f"INFO [{site_name}] 価格取得成功: {price} (from '{price_selector_used}', text: '{price_text_found_detail}')"
                                )
                            # else:
                            # print(f"DEBUG [{site_name}] 価格抽出失敗。Item text: '{item_text_content[:100].replace('\n', ' ')}...'")

                        except StaleElementReferenceException:
                            print(
                                f"{datetime.datetime.now()} WARN [{site_name}] アイテム処理中にStaleElement。スキップ。"
                            )
                            continue
                        except Exception as e_item_proc:
                            print(
                                f"{datetime.datetime.now()} ERROR [{site_name}] アイテム個別処理中: {type(e_item_proc).__name__} - {e_item_proc}"
                            )

                        time.sleep(random.uniform(0.01, 0.05))

                    if items_collected_count >= max_items_to_collect:
                        break

                except TimeoutException:
                    print(
                        f"{datetime.datetime.now()} INFO [{site_name}] セレクタ '{container_selector}' で要素待機タイムアウト。"
                    )
                    continue
                except Exception as e_container_loop:
                    print(
                        f"{datetime.datetime.now()} ERROR [{site_name}] アイテムコンテナ探索/処理中: {e_container_loop}"
                    )

            if items_collected_count >= max_items_to_collect:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 目標取得数 {max_items_to_collect} 件に到達。"
                )
                break
            if not new_items_found_this_scroll and scroll_count_done >= max_scrolls:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 今回のスクロールで新アイテムなし、かつ最大スクロール回数到達。"
                )
                break
            elif not new_items_found_this_scroll:
                print(
                    f"{datetime.datetime.now()} [{site_name}] 今回のスクロールで新アイテムなし。次のスクロールへ。"
                )

        if not prices:
            print(
                f"{datetime.datetime.now()} WARN [{site_name}] 価格データ最終的になし (0件): {keyword_to_search}"
            )

    except TimeoutException as e_page_load:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] ページ読込タイムアウト({current_page_load_timeout}秒): {keyword_to_search} - {e_page_load.msg if hasattr(e_page_load, 'msg') else e_page_load}"
        )
    except WebDriverException as e_wd_main:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] WebDriver操作中: {keyword_to_search} - {type(e_wd_main).__name__}: {e_wd_main.msg if hasattr(e_wd_main, 'msg') else e_wd_main}"
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

    try:
        df_existing = pd.DataFrame(columns=list(new_data_row.keys()))
        if file_path.exists() and os.path.getsize(file_path) > 0:
            try:
                df_existing = pd.read_csv(file_path, dtype={"date": str})
            except Exception as e_read:
                print(
                    f"{datetime.datetime.now()} WARN: {file_path} 読込失敗: {e_read}。新規作成。"
                )

        if "date" in df_existing.columns and not df_existing["date"].empty:
            try:
                df_existing["date"] = pd.to_datetime(
                    df_existing["date"], errors="coerce"
                ).dt.strftime("%Y-%m-%d")
                df_existing = df_existing.dropna(subset=["date"])
            except Exception as e_date_conv:
                print(
                    f"{datetime.datetime.now()} WARN: {file_path} date列変換で問題: {e_date_conv}"
                )

        mask = pd.Series(False, index=df_existing.index)  # 初期化
        if (
            not df_existing.empty and "date" in df_existing.columns
        ):  # date列がない場合はマスクできない
            mask = (
                (df_existing["date"] == today_str)
                & (df_existing["site"] == site_name)
                & (df_existing["keyword"] == brand_keyword)
            )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            # 更新する列のみ指定
            update_cols = ["count", "average_price", "min_price", "max_price"]
            for col_idx, col_name in enumerate(update_cols):
                df_existing.loc[existing_today_data_indices, col_name] = new_data_row[
                    col_name
                ]
            print(
                f"{datetime.datetime.now()} INFO [{site_name}] '{brand_keyword}' 本日データ更新: {file_name}"
            )
        else:
            new_df_row_df = pd.DataFrame([new_data_row])
            # df_existingが完全に空(列もない)場合を考慮
            if df_existing.empty:
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
    start_time = datetime.datetime.now()
    print(f"{start_time} 一括スクレイピング処理を開始します...")
    brands_data_all_sites = load_brands_from_json()

    if not brands_data_all_sites:
        print(
            f"{datetime.datetime.now()} ERROR: ブランド情報が読み込めなかったため、処理を終了します。"
        )
        return

    for site_name, site_brands_data in brands_data_all_sites.items():
        site_start_time = datetime.datetime.now()
        print(f"\n{site_start_time} --- サイト処理開始: {site_name} ---")

        if site_name not in SITE_CONFIGS:
            print(
                f"{datetime.datetime.now()} WARN: サイト '{site_name}' の設定がSITE_CONFIGSに存在しません。スキップします。"
            )
            continue

        for category_name, brands_in_category in site_brands_data.items():
            print(
                f"{datetime.datetime.now()}   -- カテゴリ処理中: {category_name} ({len(brands_in_category)}ブランド) --"
            )
            for brand_idx, brand_keyword in enumerate(brands_in_category):
                brand_process_start_time = datetime.datetime.now()
                print(
                    f"{brand_process_start_time}     - ブランド ({brand_idx+1}/{len(brands_in_category)}): {brand_keyword} ({site_name})"
                )

                # 特定ブランドのみテストする場合の例
                # if site_name == "mercari" and brand_keyword != "Supreme":
                #     print(f"INFO: Skipping {brand_keyword} for mercari test.")
                #     continue
                # if site_name == "rakuma" and brand_keyword != "BEAMS":
                #     print(f"INFO: Skipping {brand_keyword} for rakuma test.")
                #     continue

                prices = scrape_prices_for_keyword_and_site(site_name, brand_keyword)

                if prices:  # pricesがNoneでなく、かつ空でないリストの場合
                    save_daily_stats_for_site(site_name, brand_keyword, prices)
                else:  # pricesがNoneまたは空リストの場合
                    print(
                        f"{datetime.datetime.now()} INFO [{site_name}] ブランド '{brand_keyword}' の有効な価格情報が見つからなかったため、CSVファイルは更新/作成されません。"
                    )

                brand_process_end_time = datetime.datetime.now()
                print(
                    f"{brand_process_end_time}     - ブランド '{brand_keyword}' 処理完了。所要時間: {brand_process_end_time - brand_process_start_time}"
                )

                if brand_idx < len(brands_in_category) - 1:
                    sleep_duration = random.uniform(*INTER_BRAND_SLEEP_TIME)
                    print(
                        f"{datetime.datetime.now()}     - 次のブランドまで {sleep_duration:.1f} 秒待機..."
                    )
                    time.sleep(sleep_duration)
            print(
                f"{datetime.datetime.now()}   -- カテゴリ '{category_name}' 処理完了 --"
            )

        site_end_time = datetime.datetime.now()
        print(
            f"{site_end_time} --- サイト '{site_name}' 処理完了。所要時間: {site_end_time - site_start_time} ---"
        )
        if processed_site_count < len(
            brands_data_all_sites
        ):  # 最後のサイトでなければスリープ (processed_site_countは1から始まるため)
            site_sleep_duration = random.uniform(*INTER_SITE_SLEEP_TIME)
            print(
                f"{datetime.datetime.now()} 次のサイト処理まで {site_sleep_duration:.1f} 秒待機..."
            )
            time.sleep(site_sleep_duration)
            processed_site_count += 1  # カウンタをここでインクリメント

    overall_end_time = datetime.datetime.now()
    print(
        f"\n{overall_end_time} 全ての一括スクレイピング処理が完了しました。総所要時間: {overall_end_time - start_time}"
    )


if __name__ == "__main__":
    main_scrape_all()
