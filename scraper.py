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

# === 設定 ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BRAND_FILE = BASE_DIR / "brands.json"  # app.py側で主に使うが、パスは共通認識として持つ
URL_TEMPLATE = "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time"

# === 初期化 ===
DATA_DIR.mkdir(exist_ok=True)


# === ブラウザ設定 ===
def setup_driver():
    """Selenium WebDriverのセットアップ"""
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
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"  # User Agentは適宜更新
    )

    # WebDriverのパスを自動で取得・設定
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        # Webdriver検出を回避するためのスクリプト実行
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return driver
    except Exception as e:
        print(f"WebDriverのセットアップ中にエラーが発生しました: {e}")
        print(
            "Chromeがインストールされているか、ネットワーク接続が有効か確認してください。"
        )
        print(
            "webdriver-managerが正しく動作しない場合、手動でChromeDriverをダウンロードし、パスを指定する必要があるかもしれません。"
        )
        return None


def extract_price_from_text(text):
    """テキストから価格を抽出する関数"""
    if not text:
        return None

    # ¥記号と数字、カンマのみを抽出
    price_match = re.search(r"¥\s*([0-9,]+)", text)
    if price_match:
        price_digits = re.sub(
            r"[^0-9]", "", price_match.group(1)
        )  # group(1) で括弧内の数値部分のみ取得
        if price_digits:
            return int(price_digits)

    # ¥記号なしで数字とカンマのみの場合 (例: "15,000")
    digits_only_match = re.fullmatch(r"[0-9,]+", text.strip())
    if digits_only_match:
        price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
        if price_digits:
            return int(price_digits)

    return None


def scrape_prices_for_keyword(keyword, max_items=20):
    """指定されたキーワードでメルカリから価格リストを取得する"""
    driver = setup_driver()
    if not driver:
        return []  # WebDriverのセットアップに失敗した場合

    prices = []
    try:
        url = URL_TEMPLATE.format(keyword=keyword)
        driver.get(url)
        print(f"ページを読み込み中: {url}")

        # ページが完全に読み込まれるまで少し待機 (動的コンテンツ対策)
        time.sleep(random.uniform(3, 5))

        # ランダムなスクロールを実行してボットっぽさを軽減
        for _ in range(random.randint(2, 4)):
            scroll_height = random.randint(400, 800)
            driver.execute_script(f"window.scrollBy(0, {scroll_height});")
            time.sleep(random.uniform(0.8, 1.8))

        # 商品アイテムのメインコンテナ要素のセレクタ候補
        item_container_selectors = [
            'li[data-testid="item-cell"]',  # メルカリの主要な商品セル
            'div[data-testid="item-cell"]',
            "mer-item-thumbnail",  # サムネイルコンポーネント
            ".merListItem",  # 古いUIの名残の可能性
            # '.item-cell', # 一般的なクラス名
        ]

        # 価格要素のセレクタ候補 (商品コンテナの内部で検索)
        price_inner_selectors = [
            '[data-testid="price"]',
            '[class*="Price"]',  # "Price"を含むクラス名
            ".merPrice",
            'span[class*="price"]',  # spanタグで価格表示している場合
        ]

        items_processed_count = 0
        attempts = 0
        max_attempts = 3  # ページ構造が不安定な場合のリトライ

        while items_processed_count < max_items and attempts < max_attempts:
            found_items_in_current_attempt = False
            for container_selector in item_container_selectors:
                try:
                    # 商品コンテナ要素を取得
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
                        f"セレクタ '{container_selector}' で {len(item_elements)} 件の候補を検出"
                    )
                    found_items_in_current_attempt = True

                    for item_el in item_elements:
                        if items_processed_count >= max_items:
                            break

                        item_text_content = item_el.text  # アイテム全体のテキストを取得
                        price = None

                        # まずコンテナ内の価格セレクタで探す
                        for price_selector in price_inner_selectors:
                            try:
                                price_el = item_el.find_element(
                                    By.CSS_SELECTOR, price_selector
                                )
                                price_text = price_el.text
                                extracted_p = extract_price_from_text(price_text)
                                if extracted_p:
                                    price = extracted_p
                                    # print(f"  価格取得 (内部セレクタ '{price_selector}'): ¥{price}")
                                    break
                            except NoSuchElementException:
                                continue  # この価格セレクタでは見つからなかった

                        # 見つからなければアイテム全体のテキストから抽出を試みる
                        if not price and item_text_content:
                            extracted_p = extract_price_from_text(item_text_content)
                            if extracted_p:
                                price = extracted_p
                                # print(f"  価格取得 (アイテム全体テキスト): ¥{price}")

                        if price:
                            prices.append(price)
                            items_processed_count += 1

                        # StaleElement対策として短い待機
                        time.sleep(random.uniform(0.05, 0.1))

                    if items_processed_count >= max_items:
                        break  # 外側のループも抜ける
                except TimeoutException:
                    print(
                        f"タイムアウト: セレクタ '{container_selector}' で商品コンテナが見つかりません。"
                    )
                    continue  # 次のコンテナセレクタへ
                except StaleElementReferenceException:
                    print(
                        "StaleElementReferenceExceptionが発生。要素が変更されたためリトライします。"
                    )
                    break  # item_elementsのループを抜け、再取得を試みる
                except Exception as e:
                    print(f"商品処理中に予期せぬエラー: {e}")
                    continue

            if items_processed_count >= max_items or not found_items_in_current_attempt:
                break  # 目標数に達したか、何も見つからなければ終了

            # さらに商品を読み込むためにスクロール (まだ目標数に達していない場合)
            if items_processed_count < max_items:
                driver.execute_script(
                    f"window.scrollBy(0, {random.randint(600,1000)});"
                )
                time.sleep(random.uniform(1, 2))
            attempts += 1

        if not prices:
            print(f"価格データが見つかりませんでした: {keyword}")
            # ページソースの一部を出力してデバッグ情報とする
            # page_source_snippet = driver.page_source[:1000]
            # print(f"現在のページソース (先頭1000文字):\n{page_source_snippet}...")

    except TimeoutException:
        print(f"タイムアウト: ページが読み込めませんでした - {keyword} ({url})")
    except Exception as e:
        print(f"スクレイピング中にエラーが発生しました ({keyword}): {e}")
    finally:
        if driver:
            driver.quit()

    print(f"キーワード '{keyword}' で {len(prices)} 件の価格を取得しました。")
    return prices


def save_daily_stats(keyword, prices):
    """取得した価格リストから統計情報を計算し、CSVに保存する"""
    if not prices:
        print(f"保存する価格データがありません: {keyword}")
        return

    today_str = datetime.date.today().isoformat()

    # ファイル名をキーワードから生成 (ファイル名として不適切な文字を置換)
    safe_filename_keyword = re.sub(r'[\\/*?:"<>|]', "_", keyword)
    file_path = DATA_DIR / f"{safe_filename_keyword}.csv"

    count = len(prices)
    average_price = mean(prices) if prices else 0
    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    file_exists = file_path.exists()

    new_data_row = {
        "date": today_str,
        "keyword": keyword,  # 元のキーワードも保存しておくと便利
        "count": count,
        "average_price": round(average_price, 2),
        "min_price": min_price,
        "max_price": max_price,
    }

    if file_exists:
        # 既存ファイルがある場合、今日の日付のデータが既に存在するか確認
        df = pd.read_csv(file_path)
        if today_str in df["date"].values:
            # 今日のデータが既に存在する場合、更新する (例: 1日のうち複数回実行した場合)
            df.loc[
                df["date"] == today_str,
                ["count", "average_price", "min_price", "max_price"],
            ] = [count, round(average_price, 2), min_price, max_price]
            df.to_csv(file_path, index=False, encoding="utf-8")
            print(f"'{keyword}' の本日のデータを更新しました: {file_path}")
            return
        # 今日のデータがない場合は追記 (通常はこちら)

    # 新規書き込みまたは追記
    try:
        with open(file_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=new_data_row.keys())
            if (
                not file_exists or os.path.getsize(file_path) == 0
            ):  # 新規または空ファイルの場合ヘッダー書き込み
                writer.writeheader()
            writer.writerow(new_data_row)
        print(f"'{keyword}' の価格統計を保存しました: {file_path}")
    except IOError as e:
        print(f"CSVファイルへの書き込みエラー ({file_path}): {e}")


# --- Pandasのインポートをsave_daily_stats内で局所化、またはファイル先頭に移動 ---
# このファイルを直接実行する場合のテスト用 main などは省略します。
# Streamlitアプリ (app.py) からこれらの関数を呼び出すことを想定しています。
try:
    import pandas as pd
except ImportError:
    print(
        "Pandasがインストールされていません。`pip install pandas` を実行してください。"
    )
    # Pandasがない場合、save_daily_stats の既存データ更新ロジックは簡略化またはエラーとする
    pass  # ここではエラーとせず、save_daily_stats内でフォールバック処理を検討するか、必須とする
