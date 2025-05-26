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
import pandas as pd

# === 設定 ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
# BRAND_FILE は app.py 側で定義・使用

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
    "rakuma": {  # 将来のサイト追加用テンプレート
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",
        "item_container_selectors": [".item-box", ".another-item-selector"],
        "price_inner_selectors": [".price", ".item-price__value"],
        "max_items_to_scrape": 25,
        "wait_time_after_load": (2, 4),
        "scroll_count": (2, 3),
        "scroll_height": (500, 700),
        "scroll_wait_time": (1.0, 2.0),
    },
}

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
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"  # User Agentは適宜更新
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
    digits_only_match = re.fullmatch(
        r"[0-9,]+", text.strip()
    )  # strip() を追加して前後の空白を除去
    if digits_only_match:
        price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
        if price_digits:
            return int(price_digits)
    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    if site_name not in SITE_CONFIGS:
        print(f"エラー: サイト '{site_name}' の設定が見つかりません。")
        return []

    config = SITE_CONFIGS[site_name]
    # keyword_to_search はブランド名のみが渡される想定
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
        url = config["url_template"].format(
            keyword=keyword_to_search
        )  # ブランド名のみでURL生成
        driver.get(url)
        print(
            f"[{site_name}] ページを読み込み中 (キーワード: {keyword_to_search}): {url}"
        )

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
                        f"[{site_name}] セレクタ '{container_selector}' で {len(item_elements)} 件の候補を検出 (キーワード: {keyword_to_search})"
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
                        time.sleep(random.uniform(0.05, 0.1))  # Stale対策の短い待機
                    if items_processed_count >= max_items:
                        break
                except TimeoutException:
                    continue
                except StaleElementReferenceException:
                    break
                except Exception as e_item:  # 個別アイテム処理中のエラー
                    print(
                        f"[{site_name}] アイテム処理中エラー ({keyword_to_search}): {e_item}"
                    )
                    continue

            if items_processed_count >= max_items or not found_items_in_current_attempt:
                break
            if (
                items_processed_count < max_items
            ):  # まだ目標数に達していない場合、さらにスクロール
                driver.execute_script(
                    f"window.scrollBy(0, {random.randint(600,1000)});"
                )
                time.sleep(random.uniform(1, 2))
            attempts += 1

        if not prices:
            print(
                f"[{site_name}] 価格データが見つかりませんでした: {keyword_to_search}"
            )
    except TimeoutException:
        print(f"[{site_name}] タイムアウト: {keyword_to_search} ({url})")
    except Exception as e_main:
        print(f"[{site_name}] スクレイピングエラー ({keyword_to_search}): {e_main}")
    finally:
        if driver:
            driver.quit()

    print(
        f"[{site_name}] キーワード '{keyword_to_search}' で {len(prices)} 件の価格を取得しました。"
    )
    return prices


def save_daily_stats_for_site(
    site_name, brand_keyword, prices
):  # 引数名を brand_keyword に変更
    """取得した価格リストから統計情報を計算し、サイトとブランドキーワードに応じたCSVに保存する"""
    if not prices:
        print(f"[{site_name}] 保存する価格データがありません: {brand_keyword}")
        return

    today_str = datetime.date.today().isoformat()

    # ファイル名にサイト名とブランドキーワードを含める
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)  # ブランド名のみ
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    file_name = (
        f"{safe_site_name}_{safe_brand_keyword}.csv"  # ファイル名はサイト名_ブランド名
    )
    file_path = DATA_DIR / file_name

    count = len(prices)
    average_price = mean(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    new_data_row = {
        "date": today_str,
        "site": site_name,
        "keyword": brand_keyword,  # CSV内にもブランド名のみを記録
        "count": count,
        "average_price": round(average_price, 2),
        "min_price": min_price,
        "max_price": max_price,
    }

    try:
        df_existing = pd.DataFrame(columns=new_data_row.keys())  # 空のDFをデフォルトに
        if file_path.exists() and os.path.getsize(file_path) > 0:
            try:
                df_existing = pd.read_csv(file_path)
            except pd.errors.EmptyDataError:
                print(
                    f"警告: {file_path} は空または破損している可能性があります。新規作成します。"
                )

        # 今日の日付のデータが存在するか確認 (サイトとブランドキーワードも一致)
        mask = (
            (df_existing["date"] == today_str)
            & (df_existing["site"] == site_name)
            & (df_existing["keyword"] == brand_keyword)
        )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            # 更新
            df_existing.loc[
                existing_today_data_indices,
                ["count", "average_price", "min_price", "max_price"],
            ] = [count, round(average_price, 2), min_price, max_price]
            print(
                f"[{site_name}] '{brand_keyword}' の本日のデータを更新しました: {file_name}"
            )
        else:
            # 追記
            new_df_row = pd.DataFrame([new_data_row])
            df_existing = pd.concat([df_existing, new_df_row], ignore_index=True)
            print(
                f"[{site_name}] '{brand_keyword}' の価格統計を保存しました: {file_name}"
            )

        df_existing.to_csv(file_path, index=False, encoding="utf-8")

    except IOError as e:
        print(f"CSVファイルへの書き込みエラー ({file_path}): {e}")
    except Exception as e:
        print(f"データ保存中に予期せぬエラー ({file_path}): {e}")

