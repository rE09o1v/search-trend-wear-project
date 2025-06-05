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
PAGE_LOAD_TIMEOUT_SECONDS = 45  # 少し短縮
ELEMENT_WAIT_TIMEOUT_SECONDS = 15  # 少し短縮

# --- サイト別設定 ---
SITE_CONFIGS = {
    "mercari": {
        "url_template": "https://jp.mercari.com/search?keyword={keyword}&status=on_sale&order=desc&sort=created_time",
        "item_container_selectors": [
            'li[data-testid="item-cell"]',  # これがメインの可能性が高い
            'div[data-testid="item-cell"]',  # フォールバック
            # "mer-item-thumbnail", # 古いセレクタの可能性、一旦コメントアウト
            # ".merListItem",       # 古いセレクタの可能性、一旦コメントアウト
        ],
        "price_inner_selectors": [
            '[data-testid="price"]',
            'span[class*="ItemPrice"]',  # クラス名に"Price"を含むものを探す
            'span[class*="price"]',
        ],
        "max_items_to_scrape": 30,
        "wait_time_after_load": (2, 3),
        "scroll_count": (2, 3),  # (min, max)
        "scroll_height": (600, 1000),
        "scroll_wait_time": (1.5, 2.5),
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
    },
}

INTER_BRAND_SLEEP_TIME = (3, 7)  # デバッグのため短縮
INTER_SITE_SLEEP_TIME = (5, 10)  # デバッグのため短縮

DATA_DIR.mkdir(exist_ok=True)


def setup_driver():
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
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    options.set_capability(
        "goog:loggingPrefs", {"performance": "ALL", "browser": "ALL"}
    )

    driver = None
    try:
        print(
            f"{datetime.datetime.now()} ChromeDriverManager().install() を試行します。"
        )
        # service = Service(ChromeDriverManager(driver_version="125.0.6422.141").install()) # 特定バージョン
        service = Service(ChromeDriverManager().install())  # 自動検出に戻す
        print(f"{datetime.datetime.now()} webdriver.Chrome() を試行します。")
        driver = webdriver.Chrome(service=service, options=options)
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

    # デバッグ用に元のテキストも出力
    # print(f"DEBUG [{site_name}] extract_price_from_text に渡されたテキスト: '{text_content}'")

    # "¥ 12,345" or "¥12,345" (日本円記号が先頭)
    price_match_yen_symbol_first = re.search(r"¥\s*([0-9,]+)", text_content)
    if price_match_yen_symbol_first:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_symbol_first.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (¥先頭): '{text_content}' -> {price_digits}"
            )
            return int(price_digits)

    # "12,345 円" (円が末尾) - これも追加
    price_match_yen_word_last = re.search(r"([0-9,]+)\s*円", text_content)
    if price_match_yen_word_last:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen_word_last.group(1))
        if price_digits:
            print(
                f"DEBUG [{site_name}] extract_price (円末尾): '{text_content}' -> {price_digits}"
            )
            return int(price_digits)

    # "12345" (数字のみ、カンマなしも考慮)
    # ただし、これが他の数字（例：商品ID、型番）と衝突しないように注意が必要
    # より厳密なパターンにするか、他のパターンでマッチしなかった場合の最終手段とする
    # 一旦、純粋な数字のみのパターンはコメントアウト（誤検出が多いため）
    # digits_only_match = re.fullmatch(r"([0-9,]+)", text_content.strip())
    # if digits_only_match:
    #     price_digits = re.sub(r"[^0-9]", "", digits_only_match.group(0))
    #     if price_digits:
    #         # このパターンで取得した場合は特に注意深くログを出す
    #         print(f"DEBUG [{site_name}] extract_price (数字のみパターン): '{text_content}' -> {price_digits}")
    #         return int(price_digits)

    # US$ xxx.xx の形式 (GitHub Actions環境で海外IPと判定された場合を考慮)
    price_match_usd = re.search(r"US\$\s*([0-9,]+\.?[0-9]*)", text_content)
    if price_match_usd:
        price_str = price_match_usd.group(1).replace(",", "")
        try:
            # ここではドル価格をそのまま返す（換算は別途検討）
            # または、この場合はNoneを返して日本円のアイテムのみを対象とする
            print(
                f"DEBUG [{site_name}] US$表記の価格を検出: '{text_content}' -> {price_str}. 日本円ではないためスキップします。"
            )
            return None  # または float(price_str) を返して別途処理
        except ValueError:
            print(f"DEBUG [{site_name}] US$価格の数値変換失敗: {price_str}")
            return None

    # print(f"DEBUG [{site_name}] extract_price: 価格見つからず - '{text_content}'")
    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    print(
        f"{datetime.datetime.now()} [{site_name}] スクレイピング開始: {keyword_to_search}"
    )
    if site_name not in SITE_CONFIGS:
        print(
            f"{datetime.datetime.now()} エラー: サイト '{site_name}' の設定が見つかりません。"
        )
        return []

    config = SITE_CONFIGS[site_name]
    max_items_to_collect = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )

    driver = setup_driver()
    if not driver:
        print(
            f"{datetime.datetime.now()} [{site_name}] WebDriver起動失敗のため '{keyword_to_search}' をスキップ。"
        )
        return []

    prices = []
    try:
        url = config["url_template"].format(keyword=keyword_to_search)
        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み試行 (最大{PAGE_LOAD_TIMEOUT_SECONDS}秒): {keyword_to_search} - {url}"
        )
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
        driver.get(url)
        print(
            f"{datetime.datetime.now()} [{site_name}] ページ読み込み完了: {keyword_to_search}"
        )

        time.sleep(random.uniform(*config.get("wait_time_after_load", (2, 4))))

        items_collected_count = 0
        scroll_count_done = 0
        max_scrolls = config.get("scroll_count", (1, 1))[1]  # 設定の最大スクロール回数

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
                    ):  # メインセレクタで見つからないのは問題
                        print(
                            f"WARN [{site_name}] メインセレクタ '{container_selector}' でアイテムが見つかりません。"
                        )

                    for item_el_idx, item_el in enumerate(item_elements):
                        if items_collected_count >= max_items_to_collect:
                            break
                        # print(f"{datetime.datetime.now()} [{site_name}] Item {item_el_idx+1}/{len(item_elements)}...")

                        try:
                            # Mercariの場合、価格はshadow DOM内にある可能性は低いが、item_el.textで取れないか確認
                            # item_html_for_debug = item_el.get_attribute('outerHTML')
                            # print(f"DEBUG [{site_name}] Item HTML (一部): {item_html_for_debug[:300].replace('\n', ' ')}")

                            item_text_content = (
                                item_el.text
                            )  # まずアイテム全体のテキストを取得
                            # print(f"DEBUG [{site_name}] Item text content: '{item_text_content[:100].replace('\n',' ')}'")

                            price = None
                            price_selector_used = "N/A"
                            price_text_found = "N/A"

                            for p_selector in config["price_inner_selectors"]:
                                try:
                                    price_el = item_el.find_element(
                                        By.CSS_SELECTOR, p_selector
                                    )
                                    price_text_found = price_el.text.strip()
                                    if price_text_found:  # テキストが空でないことを確認
                                        extracted_p = extract_price_from_text(
                                            price_text_found, site_name
                                        )
                                        if extracted_p is not None:
                                            price = extracted_p
                                            price_selector_used = p_selector
                                            print(
                                                f"{datetime.datetime.now()} [{site_name}] 価格発見 ('{p_selector}'): '{price_text_found}' -> {price}"
                                            )
                                            break  # 価格セレクタのループを抜ける
                                except NoSuchElementException:
                                    continue
                                except StaleElementReferenceException:
                                    print(
                                        f"{datetime.datetime.now()} WARN [{site_name}] 価格要素取得中にStaleElement ('{p_selector}')"
                                    )
                                    break

                            if (
                                price is None and item_text_content
                            ):  # price_inner_selectorsで見つからなかった場合のフォールバック
                                extracted_p_fallback = extract_price_from_text(
                                    item_text_content, site_name
                                )
                                if extracted_p_fallback is not None:
                                    price = extracted_p_fallback
                                    price_selector_used = "item_el.text (fallback)"
                                    print(
                                        f"{datetime.datetime.now()} [{site_name}] 価格発見 (フォールバック item.text): {price}"
                                    )

                            if price is not None:
                                prices.append(price)
                                items_collected_count += 1
                                new_items_found_this_scroll = True
                            # else:
                            # print(f"DEBUG [{site_name}] 価格抽出失敗。Item text: '{item_text_content[:100].replace('\n', ' ')}...'")

                        except StaleElementReferenceException:
                            print(
                                f"{datetime.datetime.now()} WARN [{site_name}] アイテム処理中にStaleElement。スキップ。"
                            )
                            continue
                        except Exception as e_item_proc:
                            print(
                                f"{datetime.datetime.now()} ERROR [{site_name}] アイテム個別処理中: {e_item_proc}"
                            )

                        time.sleep(random.uniform(0.01, 0.05))  # アイテム間の超短い待機

                    if items_collected_count >= max_items_to_collect:
                        break  # item_container_selectorsのループも抜ける

                except TimeoutException:
                    print(
                        f"{datetime.datetime.now()} INFO [{site_name}] セレクタ '{container_selector}' で要素待機タイムアウト (通常動作の可能性あり)。"
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
                f"{datetime.datetime.now()} [{site_name}] 価格データ最終的になし (0件): {keyword_to_search}"
            )

    except TimeoutException as e_page_load:
        print(
            f"{datetime.datetime.now()} ERROR [{site_name}] ページ読込タイムアウト({PAGE_LOAD_TIMEOUT_SECONDS}秒): {keyword_to_search} - {e_page_load}"
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
            f"{datetime.datetime.now()} [{site_name}] 保存する価格データなし: {brand_keyword}"
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
                df_existing = pd.read_csv(
                    file_path, dtype={"date": str}
                )  # dateを文字列として読み込む
            except pd.errors.EmptyDataError:
                print(f"{datetime.datetime.now()} WARN: {file_path} は空。新規作成。")
            except Exception as e_read:
                print(
                    f"{datetime.datetime.now()} WARN: {file_path} 読込失敗: {e_read}。新規作成。"
                )

        # date列の形式を 'YYYY-MM-DD' に統一 (読み込んだ後)
        if "date" in df_existing.columns and not df_existing["date"].empty:
            try:
                # 既に正しい形式なら何もしない、そうでなければ変換を試みる
                df_existing["date"] = pd.to_datetime(
                    df_existing["date"], errors="coerce"
                ).dt.strftime("%Y-%m-%d")
                df_existing = df_existing.dropna(subset=["date"])  # 変換失敗行は削除
            except Exception as e_date_conv:
                print(
                    f"{datetime.datetime.now()} WARN: {file_path} date列変換で問題: {e_date_conv}"
                )

        # マスクの前にdf_existingの型を確認
        # print(f"DEBUG: df_existing['date'] type: {df_existing['date'].dtype if 'date' in df_existing else 'N/A'}")
        # print(f"DEBUG: today_str type: {type(today_str)}")

        mask = (
            (df_existing["date"] == today_str)
            & (df_existing["site"] == site_name)
            & (df_existing["keyword"] == brand_keyword)
        )
        existing_today_data_indices = df_existing[mask].index

        if not existing_today_data_indices.empty:
            df_existing.loc[
                existing_today_data_indices, list(new_data_row.keys())[3:]
            ] = list(new_data_row.values())[3:]
            print(
                f"{datetime.datetime.now()} [{site_name}] '{brand_keyword}' 本日データ更新: {file_name}"
            )
        else:
            new_df_row_df = pd.DataFrame([new_data_row])
            df_existing = pd.concat([df_existing, new_df_row_df], ignore_index=True)
            print(
                f"{datetime.datetime.now()} [{site_name}] '{brand_keyword}' 新規価格統計保存: {file_name}"
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
        print(f"{datetime.datetime.now()} {BRAND_FILE} を正常に読み込みました。")
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
            f"{datetime.datetime.now()} ブランド情報が読み込めなかったため、処理を終了します。"
        )
        return

    total_sites = len(brands_data_all_sites)
    processed_site_count = 0

    for site_name, site_brands_data in brands_data_all_sites.items():
        processed_site_count += 1
        site_start_time = datetime.datetime.now()
        print(
            f"\n{site_start_time} --- サイト処理開始 ({processed_site_count}/{total_sites}): {site_name} ---"
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
            for brand_idx, brand_keyword in enumerate(brands_in_category):
                brand_process_start_time = datetime.datetime.now()
                print(
                    f"{brand_process_start_time}     - ブランド ({brand_idx+1}/{len(brands_in_category)}): {brand_keyword} ({site_name})"
                )

                prices = scrape_prices_for_keyword_and_site(site_name, brand_keyword)

                if prices:
                    save_daily_stats_for_site(site_name, brand_keyword, prices)

                brand_process_end_time = datetime.datetime.now()
                print(
                    f"{brand_process_end_time}     - ブランド '{brand_keyword}' 処理完了。所要時間: {brand_process_end_time - brand_process_start_time}"
                )

                if (
                    brand_idx < len(brands_in_category) - 1
                ):  # カテゴリ内の最後のブランドでなければスリープ
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
        if processed_site_count < total_sites:  # 最後のサイトでなければスリープ
            site_sleep_duration = random.uniform(*INTER_SITE_SLEEP_TIME)
            print(
                f"{datetime.datetime.now()} 次のサイト処理まで {site_sleep_duration:.1f} 秒待機..."
            )
            time.sleep(site_sleep_duration)

    overall_end_time = datetime.datetime.now()
    print(
        f"\n{overall_end_time} 全ての一括スクレイピング処理が完了しました。総所要時間: {overall_end_time - start_time}"
    )


if __name__ == "__main__":
    main_scrape_all()
