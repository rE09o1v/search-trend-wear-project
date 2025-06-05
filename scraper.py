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
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    StaleElementReferenceException,
)
import pandas as pd  # save_daily_stats で使用

# === 設定 ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRAND_FILE = BASE_DIR / "brands.json" # brands.jsonのパス

# --- サイト別設定 ---
SITE_CONFIGS = {
    "mercari": {
        "url_template": "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time",
        "item_container_selectors": [
            'li[data-testid="item-cell"]',
            'div[data-testid="item-cell"]',
            "mer-item-thumbnail",
            ".merListItem",
        ],
        "price_inner_selectors": [
            '[data-testid="price"]',
            '[class*="Price"]',
            ".merPrice",
            'span[class*="price"]',
        ],
        "max_items_to_scrape": 30,
        "wait_time_after_load": (3, 5),
        "scroll_count": (2, 4),
        "scroll_height": (400, 800),
        "scroll_wait_time": (0.8, 1.8),
    },
    "rakuma": {
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",
        "item_container_selectors": [".item-box", ".another-item-selector"], # '.item' は曖昧すぎるので '.item-box'などに変更を検討
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
INTER_BRAND_SLEEP_TIME = (5, 15) # ブランド間のスリープ時間（秒、ランダム範囲）
INTER_SITE_SLEEP_TIME = (15, 30) # サイト間のスリープ時間（秒、ランダム範囲）


# === 初期化 ===
DATA_DIR.mkdir(exist_ok=True)


def setup_driver():
    """WebDriverをセットアップして返す"""
    options = Options()
    options.add_argument("--headless=new") # ヘッドレスモード
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
    try:
        # ChromeDriverManagerを使用してWebDriverを自動的にダウンロードおよび管理
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        # WebDriverであることを隠蔽するJavaScriptを実行
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        print("WebDriverのセットアップが完了しました。")
        return driver
    except ValueError as ve:
        print(f"WebDriverのセットアップ中にValueErrorが発生しました（Chromedriverのバージョン関連の可能性）: {ve}")
        print("ChromedriverのバージョンとChromeブラウザのバージョンが一致しているか確認してください。")
        print("または、webdriver-managerが適切なバージョンのChromedriverをダウンロード・管理できているか確認してください。")
        return None
    except Exception as e:
        print(f"WebDriverのセットアップ中に予期せぬエラーが発生しました: {e}")
        return None


def extract_price_from_text(text):
    """テキストから価格情報を抽出する"""
    if not text:
        return None
    # "¥ 12,345" のような形式
    price_match = re.search(r"¥\s*([0-9,]+)", text)
    if price_match:
        price_digits = re.sub(r"[^0-9]", "", price_match.group(1))
        if price_digits:
            return int(price_digits)
    # "12,345" のような数字のみの形式（価格専用要素で使われることがある）
    digits_only_match = re.fullmatch(
        r"[0-9,]+", text.strip()
    )
    if digits_only_match:
        price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
        if price_digits:
            return int(price_digits)
    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    """指定されたサイトとキーワード（ブランド名）で価格情報をスクレイピングする"""
    if site_name not in SITE_CONFIGS:
        print(f"エラー: サイト '{site_name}' の設定が見つかりません。")
        return []

    config = SITE_CONFIGS[site_name]
    max_items = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )

    driver = setup_driver()
    if not driver:
        print(f"[{site_name}] WebDriverの起動に失敗したため、'{keyword_to_search}' のスクレイピングをスキップします。")
        return []

    prices = []
    try:
        # ブランド名のみでURL生成
        url = config["url_template"].format(keyword=keyword_to_search)
        driver.get(url)
        print(
            f"[{site_name}] ページを読み込み中 (キーワード: {keyword_to_search}): {url}"
        )

        # ページロード後の待機
        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 4))))

        # ランダム回数スクロール
        for _ in range(random.randint(*config.get("scroll_count", (1, 3)))):
            scroll_h = random.randint(*config.get("scroll_height", (300, 700)))
            driver.execute_script(f"window.scrollBy(0, {scroll_h});")
            time.sleep(random.uniform(*config.get("scroll_wait_time", (0.5, 1.5))))

        items_processed_count = 0
        attempts = 0
        max_attempts = 3 # アイテム取得のリトライ上限

        # 最大取得アイテム数に達するか、リトライ上限に達するまでループ
        while items_processed_count < max_items and attempts < max_attempts:
            found_items_in_current_attempt = False
            # 設定されたアイテムコンテナセレクタを順番に試す
            for container_selector in config["item_container_selectors"]:
                try:
                    # 要素が表示されるまで最大15秒待機
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, container_selector)
                        )
                    )
                    item_elements = driver.find_elements(
                        By.CSS_SELECTOR, container_selector
                    )

                    if not item_elements:
                        continue # このセレクタでは見つからなかったので次へ

                    print(
                        f"[{site_name}] セレクタ '{container_selector}' で {len(item_elements)} 件の候補を検出 (キーワード: {keyword_to_search})"
                    )
                    found_items_in_current_attempt = True

                    for item_el in item_elements:
                        if items_processed_count >= max_items:
                            break # 最大取得数に達したらループを抜ける
                        
                        item_text_content = ""
                        try:
                            item_text_content = item_el.text # Stale対策のためtry-except
                        except StaleElementReferenceException:
                            print(f"[{site_name}] アイテムのテキスト取得中にStaleElementReferenceExceptionが発生。スキップします。")
                            continue


                        price = None
                        # 設定された価格内部セレクタを順番に試す
                        for price_selector in config["price_inner_selectors"]:
                            try:
                                price_el = item_el.find_element(
                                    By.CSS_SELECTOR, price_selector
                                )
                                extracted_p = extract_price_from_text(price_el.text)
                                if extracted_p:
                                    price = extracted_p
                                    break # 価格が見つかったので内部ループを抜ける
                            except NoSuchElementException:
                                continue # このセレクタでは価格が見つからない
                            except StaleElementReferenceException:
                                # 要素が古くなった場合は、このアイテムの価格取得を諦める
                                print(f"[{site_name}] 価格要素の取得中にStaleElementReferenceExceptionが発生。")
                                break 
                        
                        # アイテム全体のテキストからも価格抽出を試みる (フォールバック)
                        if not price and item_text_content:
                            extracted_p = extract_price_from_text(item_text_content)
                            if extracted_p:
                                price = extracted_p
                        
                        if price:
                            prices.append(price)
                            items_processed_count += 1
                        
                        # StaleElementReferenceException対策の短い待機
                        time.sleep(random.uniform(0.05, 0.1)) 
                    
                    if items_processed_count >= max_items:
                        break # 外側のループも抜ける
                
                except TimeoutException:
                    # 要素が見つからなかった場合（タイムアウト）
                    # print(f"[{site_name}] セレクタ '{container_selector}' でタイムアウト (キーワード: {keyword_to_search})")
                    continue
                except StaleElementReferenceException:
                    print(f"[{site_name}] アイテムリスト処理中にStaleElementReferenceException。この試行を中断。")
                    break # このセレクタでの処理を中断し、次の試行へ
                except Exception as e_item:
                    print(
                        f"[{site_name}] アイテム処理中エラー ({container_selector}, {keyword_to_search}): {e_item}"
                    )
                    continue # 次のアイテムへ

            if items_processed_count >= max_items or not found_items_in_current_attempt:
                # 目標数に達したか、現在の試行で見つからなければ終了
                break
            
            if items_processed_count < max_items : # まだ目標数に達していない場合、さらにスクロールしてリトライ
                print(f"[{site_name}] 目標取得数{max_items}に未達 ({items_processed_count}件)。再スクロールして試行します。")
                driver.execute_script(
                    f"window.scrollBy(0, {random.randint(600,1000)});"
                )
                time.sleep(random.uniform(1, 2)) # スクロール後の待機
            attempts += 1

        if not prices:
            print(
                f"[{site_name}] 価格データが見つかりませんでした: {keyword_to_search}"
            )
    except TimeoutException:
        print(f"[{site_name}] ページ読み込みタイムアウト: {keyword_to_search} ({url})")
    except Exception as e_main:
        print(f"[{site_name}] スクレイピング中に予期せぬエラー ({keyword_to_search}): {e_main}")
    finally:
        if driver:
            driver.quit()
            print(f"[{site_name}] WebDriverを終了しました ({keyword_to_search})。")

    print(
        f"[{site_name}] キーワード '{keyword_to_search}' で {len(prices)} 件の価格を取得しました。"
    )
    return prices


def save_daily_stats_for_site(
    site_name, brand_keyword, prices
):
    """取得した価格リストから統計情報を計算し、サイトとブランドキーワードに応じたCSVに保存する"""
    if not prices:
        print(f"[{site_name}] 保存する価格データがありません (キーワード: {brand_keyword})")
        return

    today_str = datetime.date.today().isoformat()

    # ファイル名に使えない文字を置換
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = (
        f"{safe_site_name}_{safe_brand_keyword}.csv"
    )
    file_path = DATA_DIR / file_name

    count = len(prices)
    average_price = mean(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    # 保存するデータ行
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
        df_existing = pd.DataFrame(columns=list(new_data_row.keys())) # 空のDFをデフォルトに
        if file_path.exists() and os.path.getsize(file_path) > 0:
            try:
                df_existing = pd.read_csv(file_path)
            except pd.errors.EmptyDataError:
                print(
                    f"警告: {file_path} は空または破損している可能性があります。新規作成します。"
                )
            except Exception as e_read:
                 print(f"警告: {file_path} の読み込みに失敗しました: {e_read}。新規作成します。")


        # date列をdatetime型に変換（存在する場合）
        if "date" in df_existing.columns:
            try:
                df_existing["date"] = pd.to_datetime(df_existing["date"]).dt.strftime('%Y-%m-%d')
            except Exception as e_date_conv:
                print(f"警告: {file_path} のdate列の変換に失敗しました: {e_date_conv}。処理を続行します。")


        # 今日の日付のデータが存在するか確認 (サイトとブランドキーワードも一致)
        mask = (
            (df_existing["date"] == today_str)
            & (df_existing["site"] == site_name)
            & (df_existing["keyword"] == brand_keyword)
        )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            # 本日のデータが存在すれば更新
            df_existing.loc[
                existing_today_data_indices,
                ["count", "average_price", "min_price", "max_price"],
            ] = [count, round(average_price, 2), min_price, max_price]
            print(
                f"[{site_name}] '{brand_keyword}' の本日のデータを更新しました: {file_name}"
            )
        else:
            # 本日のデータが存在しなければ追記
            new_df_row = pd.DataFrame([new_data_row])
            df_existing = pd.concat([df_existing, new_df_row], ignore_index=True)
            print(
                f"[{site_name}] '{brand_keyword}' の新しい価格統計を保存しました: {file_name}"
            )
        
        # date列でソート (降順にし、重複があれば新しい方を残すようにする準備)
        if "date" in df_existing.columns:
            df_existing = df_existing.sort_values(by="date", ascending=False)
            # site, keyword, date の組み合わせで重複を削除 (最新のものを残す)
            df_existing = df_existing.drop_duplicates(subset=['site', 'keyword', 'date'], keep='first')
            # 再度昇順にソートして保存
            df_existing = df_existing.sort_values(by="date", ascending=True)


        df_existing.to_csv(file_path, index=False, encoding="utf-8")

    except IOError as e:
        print(f"CSVファイルへの書き込みエラー ({file_path}): {e}")
    except Exception as e:
        print(f"データ保存中に予期せぬエラー ({file_path}): {e}")


def load_brands_from_json():
    """brands.jsonを読み込んでブランドデータを返す"""
    if not BRAND_FILE.exists():
        print(f"エラー: {BRAND_FILE} が見つかりません。")
        return {}
    try:
        with open(BRAND_FILE, "r", encoding="utf-8") as f:
            brands_data = json.load(f)
        print(f"{BRAND_FILE} を正常に読み込みました。")
        return brands_data
    except json.JSONDecodeError as e:
        print(f"エラー: {BRAND_FILE} のJSON形式が正しくありません: {e}")
        return {}
    except Exception as e:
        print(f"エラー: {BRAND_FILE} の読み込み中にエラーが発生しました: {e}")
        return {}

def main_scrape_all():
    """brands.jsonに記載された全てのブランドの価格情報をスクレイピングして保存する"""
    print("一括スクレイピング処理を開始します...")
    brands_data_all_sites = load_brands_from_json()

    if not brands_data_all_sites:
        print("ブランド情報が読み込めなかったため、処理を終了します。")
        return

    total_sites = len(brands_data_all_sites)
    site_count = 0

    for site_name, site_brands_data in brands_data_all_sites.items():
        site_count += 1
        print(f"\n--- サイト処理開始 ({site_count}/{total_sites}): {site_name} ---")
        
        if site_name not in SITE_CONFIGS:
            print(f"警告: サイト '{site_name}' の設定がSITE_CONFIGSに存在しません。スキップします。")
            continue

        total_categories = len(site_brands_data)
        category_count = 0
        for category_name, brands_in_category in site_brands_data.items():
            category_count += 1
            print(f"  -- カテゴリ処理中 ({category_count}/{total_categories}): {category_name} ({len(brands_in_category)}ブランド) --")
            
            brand_in_category_count = 0
            for brand_keyword in brands_in_category:
                brand_in_category_count += 1
                print(f"    - ブランド処理中 ({brand_in_category_count}/{len(brands_in_category)}): {brand_keyword}")
                
                # スクレイピング実行
                prices = scrape_prices_for_keyword_and_site(site_name, brand_keyword)
                
                if prices:
                    # データ保存
                    save_daily_stats_for_site(site_name, brand_keyword, prices)
                else:
                    print(f"    - ブランド '{brand_keyword}' ({site_name}) の価格情報は見つかりませんでした。")
                
                # ブランド間のスリープ
                if brand_in_category_count < len(brands_in_category): # 最後のブランドでなければスリープ
                    sleep_duration = random.uniform(*INTER_BRAND_SLEEP_TIME)
                    print(f"    - 次のブランド処理まで {sleep_duration:.1f} 秒待機します...")
                    time.sleep(sleep_duration)
            
            print(f"  -- カテゴリ '{category_name}' の処理完了 --")

        print(f"--- サイト '{site_name}' の処理完了 ---")
        # サイト間のスリープ
        if site_count < total_sites: # 最後のサイトでなければスリープ
            site_sleep_duration = random.uniform(*INTER_SITE_SLEEP_TIME)
            print(f"次のサイト処理まで {site_sleep_duration:.1f} 秒待機します...")
            time.sleep(site_sleep_duration)

    print("\n全ての一括スクレイピング処理が完了しました。")


if __name__ == "__main__":
    # このスクリプトが直接実行された場合に、一括スクレイピング処理を実行
    main_scrape_all()
