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
from selenium.webdriver.chrome.options import Options as ChromeOptions # Renamed to avoid conflict with streamlit_selenium.ChromeOptions if any, though typically not needed.
# from selenium.webdriver.chrome.service import Service # streamlit-selenium を使う場合、直接Serviceを使わないことが多い
# from webdriver_manager.chrome import ChromeDriverManager # streamlit-selenium を使う場合、これは不要
from streamlit_selenium import Chrome # streamlit-selenium からインポート
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
            "mer-item-thumbnail", # This might be too generic, ensure it's specific enough
            ".merListItem", # Older selector, might be deprecated
        ],
        "price_inner_selectors": [
            '[data-testid="price"]',
            '[class*="Price"]', # Generic class selector
            ".merPrice", # Older selector
            'span[class*="price"]', # Generic span with class containing "price"
        ],
        "max_items_to_scrape": 30, # Default number of items to try and get
        "wait_time_after_load": (3, 5), # (min_seconds, max_seconds)
        "scroll_count": (2, 4), # (min_scrolls, max_scrolls)
        "scroll_height": (400, 800), # (min_pixels, max_pixels) per scroll
        "scroll_wait_time": (0.8, 1.8), # (min_seconds, max_seconds) after each scroll
    },
    "rakuma": { # Placeholder for Rakuma
        "url_template": "https://fril.jp/s?query={keyword}&sort=created_at&order=desc",
        "item_container_selectors": [".item-box", ".another-item-selector"], # Example selectors
        "price_inner_selectors": [".price", ".item-price__value"], # Example selectors
        "max_items_to_scrape": 25,
        "wait_time_after_load": (2, 4),
        "scroll_count": (2, 3),
        "scroll_height": (500, 700),
        "scroll_wait_time": (1.0, 2.0),
    },
}

DATA_DIR.mkdir(exist_ok=True)


def setup_driver():
    """Initializes and returns a Selenium WebDriver instance using streamlit-selenium."""
    # Configure Chrome options for headless browsing and to appear more like a regular user
    options = ChromeOptions()
    options.add_argument("--headless") # Essential for server environments like Streamlit Cloud
    options.add_argument("--disable-gpu") # Often recommended for headless
    options.add_argument("--window-size=1920,1080") # Define a virtual display size
    options.add_argument("--no-sandbox") # Required for running as root/in containers
    options.add_argument("--disable-dev-shm-usage") # Overcomes resource limits in containers
    options.add_argument("--disable-blink-features=AutomationControlled") # Helps avoid bot detection
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36" # Set a common user agent
    )
    
    try:
        # streamlit_selenium.Chrome attempts to set up ChromeDriver and Chrome binary
        # automatically in environments like Streamlit Cloud.
        # The 'linux' argument might be helpful, but often not strictly necessary
        # as the library tries to detect the environment.
        driver = Chrome(
            options=options,
            # headless=True, # Already set in options, but can be explicit
            # linux=True # Explicitly state it's a Linux environment (Streamlit Cloud is)
            )
        # Further attempt to hide webdriver presence
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        print("WebDriver (using streamlit-selenium) setup completed successfully.")
        return driver
    except Exception as e:
        print(f"Error during WebDriver (streamlit-selenium) setup: {e}")
        # For more detailed debugging:
        # import traceback
        # print(traceback.format_exc())
        return None


def extract_price_from_text(text_content):
    """Extracts a numerical price from a string. Handles '¥' symbol and commas."""
    if not text_content:
        return None
    # Try to find price with '¥' symbol
    price_match_yen = re.search(r"¥\s*([0-9,]+)", text_content)
    if price_match_yen:
        price_digits = re.sub(r"[^0-9]", "", price_match_yen.group(1))
        if price_digits:
            return int(price_digits)
    
    # Try to find price if it's just numbers and commas (e.g., "1,234")
    # This is a fallback if '¥' is not present or if the price is in a separate element
    # Ensure the whole string (after stripping) matches the pattern to avoid partial matches from other numbers.
    stripped_text = text_content.strip()
    if re.fullmatch(r"[0-9,]+", stripped_text):
        price_digits_no_yen = re.sub(r"[^0-9]", "", stripped_text)
        if price_digits_no_yen:
            return int(price_digits_no_yen)
            
    # Fallback for cases where the price might be the only numerical content
    # This is more risky and might pick up other numbers if not careful with selectors
    # For example, if item text is "Item Name 1234", this might pick up 1234.
    # It's generally better to rely on specific price selectors.
    # numbers_in_text = re.findall(r'\d+', re.sub(r',', '', stripped_text))
    # if len(numbers_in_text) == 1: # Only if there's exactly one number sequence
    #     return int(numbers_in_text[0])

    return None


def scrape_prices_for_keyword_and_site(
    site_name, keyword_to_search, max_items_override=None
):
    """Scrapes product prices for a given keyword from a specified site."""
    if site_name not in SITE_CONFIGS:
        print(f"Error: Configuration for site '{site_name}' not found.")
        return []

    config = SITE_CONFIGS[site_name]
    max_items_to_fetch = (
        max_items_override
        if max_items_override is not None
        else config.get("max_items_to_scrape", 20)
    )

    driver = setup_driver()
    if not driver:
        print(f"[{site_name}] WebDriver initialization failed. Aborting scrape for '{keyword_to_search}'.")
        return []

    scraped_prices = []
    search_url = config["url_template"].format(keyword=keyword_to_search)
    
    print(f"[{site_name}] Navigating to URL for keyword '{keyword_to_search}': {search_url}")

    try:
        driver.get(search_url)
        # Wait for initial page load and dynamic content
        time.sleep(random.uniform(*config.get("wait_time_after_load", (3, 5))))

        # Perform scrolling to load more items
        num_scrolls = random.randint(*config.get("scroll_count", (1, 3)))
        for i in range(num_scrolls):
            scroll_amount = random.randint(*config.get("scroll_height", (400, 800)))
            driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
            print(f"[{site_name}] Scrolled {i+1}/{num_scrolls}, waiting...")
            time.sleep(random.uniform(*config.get("scroll_wait_time", (0.8, 1.8))))

        # Attempt to find items and extract prices
        # This loop tries different container selectors and retries if items are not immediately found
        # It's a simplified retry, more robust logic might be needed for complex sites
        
        items_found_count = 0
        # Iterate through defined item container selectors
        for container_selector in config["item_container_selectors"]:
            if items_found_count >= max_items_to_fetch:
                break # Already got enough items
            try:
                # Wait for at least one item container to be present
                WebDriverWait(driver, 15).until( # Wait up to 15 seconds
                    EC.presence_of_element_located((By.CSS_SELECTOR, container_selector))
                )
                item_elements = driver.find_elements(By.CSS_SELECTOR, container_selector)
                
                if not item_elements:
                    print(f"[{site_name}] No items found with selector '{container_selector}' for '{keyword_to_search}'.")
                    continue # Try next container selector

                print(f"[{site_name}] Found {len(item_elements)} potential items using selector '{container_selector}' for '{keyword_to_search}'.")

                for item_el in item_elements:
                    if items_found_count >= max_items_to_fetch:
                        break # Reached target number of items
                    
                    price = None
                    item_text_for_fallback = "" # Store item's full text for fallback price extraction
                    try:
                        item_text_for_fallback = item_el.text # Get once to reduce StaleElement calls
                        # Try to find price using specific inner selectors first
                        for price_selector in config["price_inner_selectors"]:
                            try:
                                price_element = item_el.find_element(By.CSS_SELECTOR, price_selector)
                                extracted_p = extract_price_from_text(price_element.text)
                                if extracted_p is not None:
                                    price = extracted_p
                                    # print(f"Debug: Price {price} from inner selector {price_selector}")
                                    break # Price found with inner selector
                            except NoSuchElementException:
                                continue # This specific price selector not found within item_el
                        
                        # If no price found with inner selectors, try extracting from the item's full text
                        if price is None and item_text_for_fallback:
                            extracted_p_fallback = extract_price_from_text(item_text_for_fallback)
                            if extracted_p_fallback is not None:
                                price = extracted_p_fallback
                                # print(f"Debug: Price {price} from fallback on item text")

                        if price is not None:
                            scraped_prices.append(price)
                            items_found_count += 1
                        else:
                            # print(f"Debug: No price found for an item. Text: '{item_text_for_fallback[:100]}...'") # Log first 100 chars
                            pass

                    except StaleElementReferenceException:
                        print(f"[{site_name}] StaleElementReferenceException for an item. Skipping it. Keyword: '{keyword_to_search}'.")
                        break # Break from processing items under current container_selector, it might be unstable
                    except Exception as e_item_proc:
                        print(f"[{site_name}] Error processing individual item: {e_item_proc}. Keyword: '{keyword_to_search}'.")
                        continue # Skip to next item element
                
                if items_found_count > 0 and len(item_elements) > 0: # If this selector yielded results
                    print(f"[{site_name}] Processed {items_found_count}/{max_items_to_fetch} items using '{container_selector}'.")
                    # If a good selector is found, we might not need to try others,
                    # but for now, it tries all to maximize chances if selectors are flaky.
                    # Consider breaking here if items_found_count is substantial.

            except TimeoutException:
                print(f"[{site_name}] Timeout waiting for items with selector '{container_selector}' for '{keyword_to_search}'.")
            except Exception as e_container_sel:
                print(f"[{site_name}] Error with container selector '{container_selector}': {e_container_sel}. Keyword: '{keyword_to_search}'.")
        
        if not scraped_prices:
            print(f"[{site_name}] No prices found for keyword '{keyword_to_search}' after trying all selectors.")

    except TimeoutException:
        print(f"[{site_name}] Page load timeout for URL: {search_url}")
    except Exception as e_scrape_main:
        print(f"[{site_name}] Main scraping error for '{keyword_to_search}': {e_scrape_main}")
        # import traceback
        # print(traceback.format_exc())
    finally:
        if driver:
            try:
                driver.quit()
                print(f"[{site_name}] WebDriver quit successfully for '{keyword_to_search}'.")
            except Exception as e_quit:
                print(f"[{site_name}] Error quitting WebDriver: {e_quit}")

    print(f"[{site_name}] Scraped {len(scraped_prices)} prices for keyword '{keyword_to_search}'.")
    return scraped_prices


def save_daily_stats_for_site(
    site_name, brand_keyword, prices_list # Renamed from 'prices' to 'prices_list' for clarity
):
    """Saves daily statistics (count, avg, min, max) for scraped prices to a CSV file."""
    if not prices_list: # Check if the list is empty
        print(f"[{site_name}] No price data to save for brand '{brand_keyword}'.")
        return

    today_iso_str = datetime.date.today().isoformat()
    
    # Sanitize brand_keyword and site_name for use in filenames
    safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", brand_keyword)
    safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", site_name)
    
    # Define CSV filename and path
    csv_filename = f"{safe_site_name}_{safe_brand_keyword}.csv"
    csv_filepath = DATA_DIR / csv_filename

    # Calculate statistics
    num_prices = len(prices_list)
    avg_price = round(mean(prices_list), 2) if prices_list else 0
    min_price = min(prices_list) if prices_list else 0
    max_price = max(prices_list) if prices_list else 0

    # Prepare new data row as a dictionary
    new_row_dict = {
        "date": today_iso_str,
        "site": site_name,
        "keyword": brand_keyword, # Original brand keyword
        "count": num_prices,
        "average_price": avg_price,
        "min_price": min_price,
        "max_price": max_price,
    }
    
    expected_columns = list(new_row_dict.keys())

    try:
        df_to_save = pd.DataFrame() # Initialize an empty DataFrame

        if csv_filepath.exists() and os.path.getsize(csv_filepath) > 0:
            try:
                df_existing = pd.read_csv(csv_filepath)
                # Ensure all expected columns exist in the loaded DataFrame
                for col in expected_columns:
                    if col not in df_existing.columns:
                        df_existing[col] = None # Add missing columns with None or a suitable default
                df_to_save = df_existing
            except pd.errors.EmptyDataError:
                print(f"Warning: CSV file {csv_filepath} is empty. A new file will be created.")
                df_to_save = pd.DataFrame(columns=expected_columns) # Ensure columns for new file
            except Exception as e_read:
                print(f"Error reading CSV {csv_filepath}: {e_read}. A new file will be created/overwritten.")
                df_to_save = pd.DataFrame(columns=expected_columns) # Fallback to new DF
        else:
            # File doesn't exist or is empty, prepare a new DataFrame with correct columns
            df_to_save = pd.DataFrame(columns=expected_columns)

        # Check if data for today, this site, and this brand already exists
        # Ensure 'date', 'site', 'keyword' columns exist before masking
        if all(col in df_to_save.columns for col in ["date", "site", "keyword"]):
            mask_today_entry = (
                (df_to_save["date"] == today_iso_str) &
                (df_to_save["site"] == site_name) &
                (df_to_save["keyword"] == brand_keyword)
            )
            indices_today_entry = df_to_save[mask_today_entry].index
        else: # If essential columns for masking are missing, assume no existing entry
            indices_today_entry = pd.Index([])


        if not indices_today_entry.empty: # If entry for today exists, update it
            update_cols = ["count", "average_price", "min_price", "max_price"]
            df_to_save.loc[indices_today_entry, update_cols] = [
                num_prices, avg_price, min_price, max_price
            ]
            print(f"[{site_name}] Updated today's data for '{brand_keyword}' in {csv_filename}.")
        else: # No entry for today, append new row
            # Convert new_row_dict to a DataFrame to ensure proper concatenation
            df_new_row = pd.DataFrame([new_row_dict])
            df_to_save = pd.concat([df_to_save, df_new_row], ignore_index=True)
            print(f"[{site_name}] Appended new data for '{brand_keyword}' to {csv_filename}.")
        
        # Ensure 'date' column is datetime for proper sorting, then sort
        if 'date' in df_to_save.columns:
            try:
                df_to_save['date'] = pd.to_datetime(df_to_save['date'])
                df_to_save = df_to_save.sort_values(by=['date', 'site', 'keyword'], ascending=[False, True, True])
            except Exception as e_date_sort:
                print(f"Warning: Could not convert 'date' column to datetime or sort: {e_date_sort}")
        
        # Save the DataFrame to CSV
        df_to_save.to_csv(csv_filepath, index=False, encoding="utf-8")

    except IOError as e_io:
        print(f"IOError writing to CSV file {csv_filepath}: {e_io}")
    except Exception as e_save:
        print(f"Unexpected error during data saving for {csv_filepath}: {e_save}")
        # import traceback
        # print(traceback.format_exc())

# --- Example Usage (for testing scraper.py directly) ---
if __name__ == "__main__":
    print("Testing scraper.py directly...")
    
    # Test configuration
    test_site = "mercari" # Change to "rakuma" to test rakuma config
    # test_keyword = "Supreme" # Example keyword
    test_keyword = "Yohji Yamamoto" # Example keyword
    
    if test_site not in SITE_CONFIGS:
        print(f"Test site '{test_site}' not configured. Exiting.")
    else:
        print(f"\n--- Scraping prices for '{test_keyword}' on '{test_site}' ---")
        retrieved_prices = scrape_prices_for_keyword_and_site(
            test_site,
            test_keyword,
            max_items_override=10 # Override to get fewer items for a quick test
        )

        if retrieved_prices:
            print(f"\n--- Retrieved {len(retrieved_prices)} prices ---")
            print(retrieved_prices)
            
            print(f"\n--- Saving daily stats for '{test_keyword}' on '{test_site}' ---")
            save_daily_stats_for_site(test_site, test_keyword, retrieved_prices)
            
            # Verify saved data (optional)
            safe_brand_keyword = re.sub(r'[\\/*?:"<>|]', "_", test_keyword)
            safe_site_name = re.sub(r'[\\/*?:"<>|]', "_", test_site)
            expected_csv_file = DATA_DIR / f"{safe_site_name}_{safe_brand_keyword}.csv"
            if expected_csv_file.exists():
                print(f"\n--- Content of {expected_csv_file} ---")
                try:
                    df_check = pd.read_csv(expected_csv_file)
                    print(df_check.tail()) # Print last few entries
                except Exception as e:
                    print(f"Error reading test CSV: {e}")
            else:
                print(f"Test CSV file {expected_csv_file} was not created.")
        else:
            print(f"No prices were retrieved for '{test_keyword}' on '{test_site}'. Stats not saved.")

    print("\n--- scraper.py test finished ---")
