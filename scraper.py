import os
import csv
import json
import time
import datetime
import random
import re
from pathlib import Path
from statistics import mean

# from statistics import stdev # stdevは現在使用されていないためコメントアウト
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager # WebDriver Managerのインポート
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException, # WebDriver関連の一般的な例外
)
import pandas as pd  # save_daily_stats で使用

# === 設定 ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRAND_FILE = BASE_DIR / "brands.json" # brands.jsonのパス
PAGE_LOAD_TIMEOUT_SECONDS = 60 # driver.get()のタイムアウト秒数
ELEMENT_WAIT_TIMEOUT_SECONDS = 20 # WebDriverWaitのタイムアウト秒数

# --- サイト別設定 ---
SITE_CONFIGS = {
    "mercari": {
        "url_template": "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time",
        "item_container_selectors": [
            'li[data-testid="item-cell"]',
            'div[data-testid="item-cell"]',
            "mer-item-thumbnail", # 古いセレクタかもしれないので注意
            ".merListItem",       # 古いセレクタかもしれないので注意
        ],
        "price_inner_selectors": [
            '[data-testid="price"]', # 最新のMercariで使われている可能性が高い
            'span[class*="price"]',  # 一般的な価格表示に使われる可能性
            '.merPrice',            # 古いセレクタかもしれない
        ],
        "max_items_to_scrape": 30,
        "wait_time_after_load": (2, 4), # ページロード後の追加待機（秒）
        "scroll_count": (2, 3),         # スクロール回数のランダム範囲
        "scroll_height": (500, 900),    # 1回のスクロール高さのランダム範囲
        "scroll_wait_time": (1.0, 2.5), # スクロール後の待機時間（秒）
    },
    "rakuma": {
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",
        "item_container_selectors": [".item-box"], # '.another-item-selector' は例なので具体的なものに
        "price_inner_selectors": [".price", ".item-price__value"],
        "max_items_to_scrape": 25,
        "wait_time_after_load": (2, 4),
        "scroll_count": (2, 3),
        "scroll_height": (500, 700),
        "scroll_wait_time": (1.0, 2.0),
    },
    # 他のサイトの設定をここに追加
}

# --- 一括スクレイピング設定 ---
INTER_BRAND_SLEEP_TIME = (5, 10) # ブランド間のスリープ時間（秒、ランダム範囲） 少し短縮
INTER_SITE_SLEEP_TIME = (10, 20) # サイト間のスリープ時間（秒、ランダム範囲） 少し短縮


# === 初期化 ===
DATA_DIR.mkdir(exist_ok=True)


def setup_driver():
    """WebDriverをセットアップして返す"""
    print(f"{datetime.datetime.now()} WebDriverセットアップ開始...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # より一般的なユーザーエージェント文字列
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    # ログレベル設定 (より詳細な情報を得るため)
    options.set_capability("goog:loggingPrefs", {'performance': 'ALL', 'browser': 'ALL'})


    driver = None
    try:
        # webdriver-managerを使用
        print(f"{datetime.datetime.now()} ChromeDriverManager().install() を試行します。")
        # service = Service(executable_path=ChromeDriverManager().install()) # 古い書き方
        service = Service(ChromeDriverManager(driver_version="125.0.6422.141").install()) # バージョンを明示的に指定してみる (実際のChromeバージョンに合わせる)
        # または、最新を自動取得する場合
        # service = Service(ChromeDriverManager().install())

        print(f"{datetime.datetime.now()} webdriver.Chrome() を試行します。")
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        print(f"{datetime.datetime.now()} WebDriverのセットアップが完了しました。Driver: {driver}")
        return driver
    except ValueError as ve: # webdriver-managerがバージョン不一致で出すことがある
        print(f"{datetime.datetime.now()} WebDriverセットアップ中にValueError: {ve}")
        print(f"{datetime.datetime.now()} Chrome/Chromedriverのバージョンを確認してください。")
        if driver: driver.quit()
        return None
    except WebDriverException as wde: # Seleniumのより一般的な例外
        print(f"{datetime.datetime.now()} WebDriverセットアップ中にWebDriverException: {wde}")
        print(f"{datetime.datetime.now()} エラー詳細: {wde.msg}")
        if driver: driver.quit()
        return None
    except Exception as e:
        print(f"{datetime.datetime.now()} WebDriverセットアップ中に予期せぬエラー: {type(e).__name__} - {e}")
        if driver: driver.quit()
        return None


def extract_price_from_text(text):
    """テキストから価格情報を抽出する"""
    if not text:
        return None
    # "¥ 12,345" or "¥12,345"
    price_match = re.search(r"¥\s*([0-9,]+)", text)
    if price_match:
        price_digits = re.sub(r"[^0-9]", "", price_match.group(1))
        if price_digits:
            return int(price_digits)
    # "12,345" (数字とカンマのみ)
    digits_only_match = re.fullmatch(r"([0-9,]+)", text.strip())
    if digits_only_match:
        price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
        if price_digits:
            return int(price_digits)
    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    """指定されたサイトとキーワード（ブランド名）で価格情報をスクレイピングする"""
    print(f"{datetime.datetime.now()} [{site_name}] スクレイピング開始: {keyword_to_search}")
    if site_name not in SITE_CONFIGS:
        print(f"{datetime.datetime.now()} エラー: サイト '{site_name}' の設定が見つかりません。")
        return []

    config = SITE_CONFIGS[site_name]
    max_items_to_collect = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )

    driver = setup_driver()
    if not driver:
        print(f"{datetime.datetime.now()} [{site_name}] WebDriver起動失敗のため '{keyword_to_search}' をスキップ。")
        return []

    prices = []
    try:
        url = config["url_template"].format(keyword=keyword_to_search)
        print(f"{datetime.datetime.now()} [{site_name}] ページ読み込み試行 (最大{PAGE_LOAD_TIMEOUT_SECONDS}秒): {keyword_to_search} - {url}")
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS) # ページロードタイムアウト設定
        driver.get(url)
        print(f"{datetime.datetime.now()} [{site_name}] ページ読み込み完了: {keyword_to_search}")

        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 4))))

        items_processed_this_run = 0
        scroll_attempts_done = 0
        max_scroll_attempts = config.get("scroll_count", (2,3))[1] # 最大スクロール回数を取得

        # スクロールとアイテム取得のループ
        # items_processed_this_run < max_items_to_collect 条件のみで制御し、
        # スクロール回数は別途カウント
        while items_processed_this_run < max_items_to_collect and scroll_attempts_done <= max_scroll_attempts :
            if scroll_attempts_done > 0: # 初回以外はスクロール
                scroll_h = random.randint(*config.get("scroll_height", (400, 800)))
                print(f"{datetime.datetime.now()} [{site_name}] スクロール実行 ({scroll_h}px)...")
                driver.execute_script(f"window.scrollBy(0, {scroll_h});")
                time.sleep(random.uniform(*config.get("scroll_wait_time", (0.8, 1.8))))
            scroll_attempts_done +=1

            found_new_items_in_this_scroll = False
            for container_selector_idx, container_selector in enumerate(config["item_container_selectors"]):
                print(f"{datetime.datetime.now()} [{site_name}] アイテムコンテナ探索: '{container_selector}' (試行 {container_selector_idx+1}/{len(config['item_container_selectors'])})")
                try:
                    WebDriverWait(driver, ELEMENT_WAIT_TIMEOUT_SECONDS).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, container_selector)
                        )
                    )
                    item_elements = driver.find_elements(
                        By.CSS_SELECTOR, container_selector
                    )
                    print(f"{datetime.datetime.now()} [{site_name}] セレクタ '{container_selector}' で {len(item_elements)} 件の候補を検出。")

                    if not item_elements:
                        continue

                    for item_idx, item_el in enumerate(item_elements):
                        if items_processed_this_run >= max_items_to_collect:
                            break
                        
                        print(f"{datetime.datetime.now()} [{site_name}] Item {item_idx+1}/{len(item_elements)} 処理中...")
                        item_text_content = ""
                        try:
                            item_text_content = item_el.text
                        except StaleElementReferenceException:
                            print(f"{datetime.datetime.now()} [{site_name}] アイテムテキスト取得中にStaleElement。スキップ。")
                            continue
                        except Exception as e_text:
                            print(f"{datetime.datetime.now()} [{site_name}] アイテムテキスト取得中に予期せぬエラー: {e_text}。スキップ。")
                            continue


                        price = None
                        price_found_from_selector = False
                        for price_selector_idx, price_selector in enumerate(config["price_inner_selectors"]):
                            try:
                                price_el = item_el.find_element( By.CSS_SELECTOR, price_selector )
                                price_text = price_el.text
                                extracted_p = extract_price_from_text(price_text)
                                if extracted_p:
                                    price = extracted_p
                                    price_found_from_selector = True
                                    print(f"{datetime.datetime.now()} [{site_name}] 価格発見 ('{price_selector}'): {price_text} -> {price}")
                                    break
                            except NoSuchElementException:
                                # print(f"DEBUG: [{site_name}] 価格セレクタ '{price_selector}' 見つからず。")
                                continue
                            except StaleElementReferenceException:
                                print(f"{datetime.datetime.now()} [{site_name}] 価格要素取得中にStaleElement ('{price_selector}')。")
                                break # このアイテムの価格セレクタ探索は中断
                            except Exception as e_price_el:
                                print(f"{datetime.datetime.now()} [{site_name}] 価格要素取得中に予期せぬエラー ('{price_selector}'): {e_price_el}")
                                break


                        if not price and item_text_content: # フォールバック
                            extracted_p_fallback = extract_price_from_text(item_text_content)
                            if extracted_p_fallback:
                                price = extracted_p_fallback
                                print(f"{datetime.datetime.now()} [{site_name}] 価格発見 (フォールバック item.text): {price}")
                        
                        if price:
                            prices.append(price)
                            items_processed_this_run += 1
                            found_new_items_in_this_scroll = True
                        else:
                            # 価格が見つからなかったアイテムの情報を少し出す
                            print(f"{datetime.datetime.now()} DEBUG: [{site_name}] 価格が見つからなかったアイテムのテキスト一部: '{item_text_content[:100].replace('\n', ' ')}...'")
                            # outer_html_snippet = item_el.get_attribute('outerHTML')[:300]
                            # print(f"DEBUG: [{site_name}] 価格が見つからなかったアイテムのHTML一部: '{outer_html_snippet.replace('\n', ' ')}...'")


                        time.sleep(random.uniform(0.05, 0.15)) # アイテム処理間の短い待機
                    
                    if items_processed_this_run >= max_items_to_collect:
                        break # item_container_selectorsのループも抜ける
                
                except TimeoutException:
                    print(f"{datetime.datetime.now()} [{site_name}] セレクタ '{container_selector}' で要素待機タイムアウト。")
                    continue
                except StaleElementReferenceException: # item_elements を取得後、ループ中に古くなる場合
                    print(f"{datetime.datetime.now()} [{site_name}] アイテムリスト処理中にStaleElement。このコンテナセレクタを再試行。")
                    break # container_selectorのループを抜け、次のスクロールへ（同じコンテナを再試行する可能性がある）
                except Exception as e_item_loop:
                    print(f"{datetime.datetime.now()} [{site_name}] アイテム処理ループ中に予期せぬエラー: {e_item_loop}")
                    continue
            
            if items_processed_this_run >= max_items_to_collect:
                print(f"{datetime.datetime.now()} [{site_name}] 目標取得数 {max_items_to_collect} に到達しました。")
                break # while ループを抜ける

            if not found_new_items_in_this_scroll and scroll_attempts_done >= max_scroll_attempts:
                print(f"{datetime.datetime.now()} [{site_name}] このスクロールで見つからず、最大スクロール回数に到達。処理終了。")
                break # while ループを抜ける
            elif not found_new_items_in_this_scroll :
                 print(f"{datetime.datetime.now()} [{site_name}] このスクロールでは新しいアイテムが見つかりませんでした。次のスクロールを試みます。")


        if not prices:
            print(f"{datetime.datetime.now()} [{site_name}] 価格データ最終的になし: {keyword_to_search}")

    except TimeoutException as e_page_load: # driver.get()のタイムアウト
        print(f"{datetime.datetime.now()} [{site_name}] ページ読み込みタイムアウト ({PAGE_LOAD_TIMEOUT_SECONDS}秒超過): {keyword_to_search} - {e_page_load}")
    except WebDriverException as e_wd_main: # driver.get()などで発生しうる一般的なWebDriverエラー
        print(f"{datetime.datetime.now()} [{site_name}] WebDriver操作中にエラー: {keyword_to_search} - {type(e_wd_main).__name__}: {e_wd_main}")
    except Exception as e_main:
        print(f"{datetime.datetime.now()} [{site_name}] スクレイピング全体で予期せぬエラー: {keyword_to_search} - {type(e_main).__name__}: {e_main}")
    finally:
        if driver:
            try:
                # ブラウザコンソールログを取得 (デバッグ用)
                # browser_logs = driver.get_log('browser')
                # if browser_logs:
                #     print(f"DEBUG: [{site_name}] Browser Console Logs for {keyword_to_search}:")
                #     for entry in browser_logs[:5]: # 最初の5件
                #         print(entry)
                driver.quit()
                print(f"{datetime.datetime.now()} [{site_name}] WebDriver終了: {keyword_to_search}")
            except Exception as e_quit:
                 print(f"{datetime.datetime.now()} [{site_name}] WebDriver終了時にエラー: {e_quit}")


    print(f"{datetime.datetime.now()} [{site_name}] キーワード '{keyword_to_search}' で {len(prices)} 件の価格を取得完了。")
    return prices


def save_daily_stats_for_site(
    site_name, brand_keyword, prices
):
    """取得した価格リストから統計情報を計算し、サイトとブランドキーワードに応じたCSVに保存する"""
    if not prices:
        print(f"{datetime.datetime.now()} [{site_name}] 保存する価格データなし: {brand_keyword}")
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
        "date": today_str, "site": site_name, "keyword": brand_keyword,
        "count": count, "average_price": round(average_price, 2),
        "min_price": min_price, "max_price": max_price,
    }

    try:
        df_existing = pd.DataFrame(columns=list(new_data_row.keys()))
        if file_path.exists() and os.path.getsize(file_path) > 0:
            try:
                df_existing = pd.read_csv(file_path)
            except pd.errors.EmptyDataError:
                print(f"{datetime.datetime.now()} 警告: {file_path} は空または破損。新規作成。")
            except Exception as e_read:
                 print(f"{datetime.datetime.now()} 警告: {file_path} 読込失敗: {e_read}。新規作成。")

        if "date" in df_existing.columns and not df_existing["date"].empty:
            try:
                df_existing["date"] = pd.to_datetime(df_existing["date"]).dt.strftime('%Y-%m-%d')
            except Exception as e_date_conv:
                print(f"{datetime.datetime.now()} 警告: {file_path} date列変換失敗: {e_date_conv}")

        mask = (
            (df_existing["date"] == today_str) &
            (df_existing["site"] == site_name) &
            (df_existing["keyword"] == brand_keyword)
        )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            df_existing.loc[existing_today_data_indices, list(new_data_row.keys())[3:]] = list(new_data_row.values())[3:]
            print(f"{datetime.datetime.now()} [{site_name}] '{brand_keyword}' 本日データ更新: {file_name}")
        else:
            new_df_row_df = pd.DataFrame([new_data_row])
            df_existing = pd.concat([df_existing, new_df_row_df], ignore_index=True)
            print(f"{datetime.datetime.now()} [{site_name}] '{brand_keyword}' 新規価格統計保存: {file_name}")
        
        if "date" in df_existing.columns and not df_existing.empty:
            df_existing = df_existing.sort_values(by="date", ascending=False)
            df_existing = df_existing.drop_duplicates(subset=['site', 'keyword', 'date'], keep='first')
            df_existing = df_existing.sort_values(by="date", ascending=True)

        df_existing.to_csv(file_path, index=False, encoding="utf-8")
    except IOError as e:
        print(f"{datetime.datetime.now()} CSV書込エラー ({file_path}): {e}")
    except Exception as e:
        print(f"{datetime.datetime.now()} データ保存中予期せぬエラー ({file_path}): {type(e).__name__} - {e}")


def load_brands_from_json():
    """brands.jsonを読み込んでブランドデータを返す"""
    if not BRAND_FILE.exists():
        print(f"{datetime.datetime.now()} エラー: {BRAND_FILE} が見つかりません。")
        return {}
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            brands_data = json.load(f)
        print(f"{datetime.datetime.now()} {BRAND_FILE} を正常に読み込みました。")
        return brands_data
    except json.JSONDecodeError as e:
        print(f"{datetime.datetime.now()} エラー: {BRAND_FILE} JSON形式不正: {e}")
        return {}
    except Exception as e:
        print(f"{datetime.datetime.now()} エラー: {BRAND_FILE} 読込中エラー: {e}")
        return {}

def main_scrape_all():
    """brands.jsonに記載された全てのブランドの価格情報をスクレイピングして保存する"""
    start_time = datetime.datetime.now()
    print(f"{start_time} 一括スクレイピング処理を開始します...")
    brands_data_all_sites = load_brands_from_json()

    if not brands_data_all_sites:
        print(f"{datetime.datetime.now()} ブランド情報が読み込めなかったため、処理を終了します。")
        return

    total_sites = len(brands_data_all_sites)
    processed_site_count = 0

    for site_name, site_brands_data in brands_data_all_sites.items():
        processed_site_count += 1
        print(f"\n{datetime.datetime.now()} --- サイト処理開始 ({processed_site_count}/{total_sites}): {site_name} ---")
        
        if site_name not in SITE_CONFIGS:
            print(f"{datetime.datetime.now()} 警告: サイト '{site_name}' の設定がSITE_CONFIGSに存在しません。スキップします。")
            continue

        total_categories_in_site = len(site_brands_data)
        processed_category_count = 0
        for category_name, brands_in_category in site_brands_data.items():
            processed_category_count += 1
            print(f"{datetime.datetime.now()}   -- カテゴリ処理中 ({processed_category_count}/{total_categories_in_site}): {category_name} ({len(brands_in_category)}ブランド) --")
            
            processed_brands_in_category = 0
            for brand_keyword in brands_in_category:
                processed_brands_in_category += 1
                brand_process_start_time = datetime.datetime.now()
                print(f"{brand_process_start_time}     - ブランド処理中 ({processed_brands_in_category}/{len(brands_in_category)}): {brand_keyword} ({site_name})")
                
                prices = scrape_prices_for_keyword_and_site(site_name, brand_keyword)
                
                if prices:
                    save_daily_stats_for_site(site_name, brand_keyword, prices)
                else:
                    print(f"{datetime.datetime.now()}     - ブランド '{brand_keyword}' ({site_name}) の価格情報は見つかりませんでした。")
                
                brand_process_end_time = datetime.datetime.now()
                print(f"{brand_process_end_time}     - ブランド '{brand_keyword}' 処理完了。所要時間: {brand_process_end_time - brand_process_start_time}")

                if processed_brands_in_category < len(brands_in_category):
                    sleep_duration = random.uniform(*INTER_BRAND_SLEEP_TIME)
                    print(f"{datetime.datetime.now()}     - 次のブランド処理まで {sleep_duration:.1f} 秒待機...")
                    time.sleep(sleep_duration)
            
            print(f"{datetime.datetime.now()}   -- カテゴリ '{category_name}' の処理完了 --")

        print(f"{datetime.datetime.now()} --- サイト '{site_name}' の処理完了 ---")
        if processed_site_count < total_sites:
            site_sleep_duration = random.uniform(*INTER_SITE_SLEEP_TIME)
            print(f"{datetime.datetime.now()} 次のサイト処理まで {site_sleep_duration:.1f} 秒待機...")
            time.sleep(site_sleep_duration)

    end_time = datetime.datetime.now()
    print(f"\n{end_time} 全ての一括スクレイピング処理が完了しました。総所要時間: {end_time - start_time}")


if __name__ == "__main__":
    main_scrape_all()
