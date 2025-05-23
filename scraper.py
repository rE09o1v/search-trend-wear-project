import os
import csv
import json
import time
import datetime
import random
import re
from pathlib import Path
from statistics import mean, stdev
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
# BRAND_FILE は app.py 側で定義・使用する想定

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
        "max_items_to_scrape": 30,  # サイトごとのデフォルト取得件数
        "wait_time_after_load": (3, 5),  # ページ読み込み後のランダム待機時間 (min, max)
        "scroll_count": (2, 4),  # スクロール回数 (min, max)
        "scroll_height": (400, 800),  # スクロール高さ (min, max)
        "scroll_wait_time": (0.8, 1.8),  # スクロール後の待機時間 (min, max)
    },
    "rakuma": {  # 将来のサイト追加用テンプレート (実際のセレクタとは異なります)
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",  # 仮
        "item_container_selectors": [".item-box", ".another-item-selector"],  # 仮
        "price_inner_selectors": [".price", ".item-price__value"],  # 仮
        "max_items_to_scrape": 25,
        "wait_time_after_load": (2, 4),
        "scroll_count": (2, 3),
        "scroll_height": (500, 700),
        "scroll_wait_time": (1.0, 2.0),
    },
    # 他のサイトの設定をここに追加
}

# === 初期化 ===
DATA_DIR.mkdir(exist_ok=True)


def setup_driver():
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
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return driver
    except Exception as e:
        print(f"WebDriverのセットアップ中にエラーが発生しました: {e}")
        return None


def extract_price_from_text(text):
    if not text:
        return None
    price_match = re.search(r"¥\s*([0-9,]+)", text)
    if price_match:
        price_digits = re.sub(r"[^0-9]", "", price_match.group(1))
        if price_digits:
            return int(price_digits)
    digits_only_match = re.fullmatch(r"[0-9,]+", text.strip())
    if digits_only_match:
        price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
        if price_digits:
            return int(price_digits)
    return None


def scrape_prices_for_keyword_and_site(site_name, keyword, max_items_override=None):
    """指定されたサイトとキーワードで価格リストを取得する"""
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
        return []

    prices = []
    try:
        url = config["url_template"].format(keyword=keyword)
        driver.get(url)
        print(f"[{site_name}] ページを読み込み中: {url}")

        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 4))))

        for _ in range(random.randint(*config.get("scroll_count", (1, 3)))):
            scroll_h = random.randint(*config.get("scroll_height", (300, 700)))
            driver.execute_script(f"window.scrollBy(0, {scroll_h});")
            time.sleep(random.uniform(*config.get("scroll_wait_time", (0.5, 1.5))))

        items_processed_count = 0
        attempts = 0
        max_attempts = 3

        while items_processed_count < max_items and attempts < max_attempts:
            found_items_in_current_attempt = False
            for container_selector in config["item_container_selectors"]:
                try:
                    WebDriverWait(driver, 15).until(
                        EC.presence_of_all_elements_located(
                            (By.CSS_SELECTOR, container_selector)
                        )
                    )
                    item_elements = driver.find_elements(
                        By.CSS_SELECTOR, container_selector
                    )

                    if not item_elements:
                        continue

                    print(
                        f"[{site_name}] セレクタ '{container_selector}' で {len(item_elements)} 件の候補を検出"
                    )
                    found_items_in_current_attempt = True

                    for item_el in item_elements:
                        if items_processed_count >= max_items:
                            break
                        item_text_content = item_el.text
                        price = None
                        for price_selector in config["price_inner_selectors"]:
                            try:
                                price_el = item_el.find_element(
                                    By.CSS_SELECTOR, price_selector
                                )
                                extracted_p = extract_price_from_text(price_el.text)
                                if extracted_p:
                                    price = extracted_p
                                    break
                            except NoSuchElementException:
                                continue

                        if not price and item_text_content:
                            extracted_p = extract_price_from_text(item_text_content)
                            if extracted_p:
                                price = extracted_p

                        if price:
                            prices.append(price)
                            items_processed_count += 1
                        time.sleep(random.uniform(0.05, 0.1))
                    if items_processed_count >= max_items:
                        break
                except TimeoutException:
                    continue
                except StaleElementReferenceException:
                    break
                except Exception:
                    continue

            if items_processed_count >= max_items or not found_items_in_current_attempt:
                break
            if items_processed_count < max_items:
                driver.execute_script(
                    f"window.scrollBy(0, {random.randint(600,1000)});"
                )
                time.sleep(random.uniform(1, 2))
            attempts += 1

        if not prices:
            print(f"[{site_name}] 価格データが見つかりませんでした: {keyword}")
    except TimeoutException:
        print(f"[{site_name}] タイムアウト: {keyword} ({url})")
    except Exception as e:
        print(f"[{site_name}] スクレイピングエラー ({keyword}): {e}")
    finally:
        if driver:
            driver.quit()

    print(
        f"[{site_name}] キーワード '{keyword}' で {len(prices)} 件の価格を取得しました。"
    )
    return prices


def save_daily_stats_for_site(site_name, keyword, prices):
    """取得した価格リストから統計情報を計算し、サイトとキーワードに応じたCSVに保存する"""
    if not prices:
        print(f"[{site_name}] 保存する価格データがありません: {keyword}")
        return

    today_str = datetime.date.today().isoformat()

    # ファイル名にサイト名とキーワードを含める
    safe_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = f"{safe_site_name}_{safe_keyword}.csv"
    file_path = DATA_DIR / file_name

    count = len(prices)
    average_price = mean(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    file_exists = file_path.exists()

    new_data_row = {
        "date": today_str,
        "site": site_name,  # サイト名も記録
        "keyword": keyword,
        "count": count,
        "average_price": round(average_price, 2),
        "min_price": min_price,
        "max_price": max_price,
    }

    # Pandas DataFrame を使用してデータの重複をチェックし、更新または追記
    try:
        if file_exists and os.path.getsize(file_path) > 0:
            df = pd.read_csv(file_path)
            # 今日の日付のデータが存在するか確認
            existing_today_data = df[
                (df["date"] == today_str)
                & (df["site"] == site_name)
                & (df["keyword"] == keyword)
            ]
            if not existing_today_data.empty:
                # 更新
                df.loc[
                    existing_today_data.index,
                    ["count", "average_price", "min_price", "max_price"],
                ] = [count, round(average_price, 2), min_price, max_price]
                df.to_csv(file_path, index=False, encoding="utf-8")
                print(
                    f"[{site_name}] '{keyword}' の本日のデータを更新しました: {file_name}"
                )
                return
        else:  # ファイルが存在しないか空の場合
            df = pd.DataFrame(columns=new_data_row.keys())  # ヘッダーを定義

        # 追記 (pd.concat を使う方が安全)
        new_df_row = pd.DataFrame([new_data_row])
        df = pd.concat([df, new_df_row], ignore_index=True)
        df.to_csv(
            file_path, index=False, encoding="utf-8"
        )  # ヘッダーは初回のみ書き込まれる
        print(f"[{site_name}] '{keyword}' の価格統計を保存しました: {file_name}")

    except IOError as e:
        print(f"CSVファイルへの書き込みエラー ({file_path}): {e}")
    except Exception as e:
        print(f"データ保存中に予期せぬエラー ({file_path}): {e}")
