#!/usr/bin/env python3
"""
Script to download hourly data from Kalshi BTC markets.
Opens browser, navigates to market, downloads data automatically.
"""
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Installing playwright...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    from playwright.sync_api import sync_playwright

MARKET_URL = "https://kalshi.com/markets/kxbtcd/bitcoin-price-abovebelow/kxbtcd-26mar0508"
OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)


def find_and_click_download(page):
    """Find and click the download/export button"""
    
    # Look for download button in various places
    # Common patterns for trading platforms
    
    # Try finding by text
    download_texts = [
        "Download", "download", "Export", "export", 
        "CSV", "Data", "Download Data"
    ]
    
    # Try different button selectors
    selectors = [
        "button:has-text('Download')",
        "button:has-text('Export')", 
        "button:has-text('CSV')",
        "[data-testid='download']",
        ".download-button",
        "[aria-label*='Download']",
    ]
    
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                print(f"Found button: {selector}")
                btn.click()
                time.sleep(2)
                return True
        except:
            continue
    
    # Try finding links
    link_selectors = ["a:has-text('Download')", "a:has-text('CSV')"]
    for selector in link_selectors:
        try:
            link = page.query_selector(selector)
            if link and link.is_visible():
                print(f"Found link: {selector}")
                link.click()
                time.sleep(2)
                return True
        except:
            continue
    
    return False


def change_frequency_to_hourly(page):
    """Change frequency/timeframe to hourly"""
    
    # Look for frequency selector
    freq_keywords = ["frequency", "timeframe", "interval", "granularity"]
    
    # Try select elements
    selects = page.query_selector_all("select")
    for sel in selects:
        try:
            label = sel.get_attribute("aria-label") or ""
            options = sel.query_selector_all("option")
            for opt in options:
                opt_text = opt.inner_text().lower()
                if "hour" in opt_text or "1h" in opt_text:
                    print(f"Found hourly option: {opt_text}")
                    sel.select_option(opt.get_attribute("value"))
                    time.sleep(1)
                    return True
        except:
            continue
    
    # Try buttons/dropdowns for timeframe
    buttons = page.query_selector_all("button")
    for btn in buttons:
        try:
            text = btn.inner_text().lower()
            if any(kw in text for kw in freq_keywords):
                btn.click()
                time.sleep(1)
                # Look for hourly option in dropdown
                items = page.query_selector_all("[role='menuitem'], .dropdown-item, li")
                for item in items:
                    item_text = item.inner_text().lower()
                    if "hour" in item_text:
                        item.click()
                        time.sleep(1)
                        return True
        except:
            continue
    
    return False


def download_with_headless_false():
    """Run with visible browser"""
    print("Starting browser...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print(f"Opening: {MARKET_URL}")
        try:
            page.goto(MARKET_URL, wait_until="domcontentloaded", timeout=60000)
            print(f"Page loaded: {page.url}")
        except Exception as e:
            print(f"Error: {e}")
        
        # Wait for page to fully load
        time.sleep(5)
        
        print(f"\nCurrent URL: {page.url}")
        
        # Check redirect
        if "kxbtcd-26mar0508" in page.url:
            print("SUCCESS: Correct market loaded - no redirect!")
        else:
            print(f"REDIRECTED to: {page.url}")
        
        # Look for download button
        print("\nLooking for download button...")
        if find_and_click_download(page):
            print("Clicked download button")
            time.sleep(2)
            
            # Try to change frequency
            print("Looking for frequency selector...")
            if change_frequency_to_hourly(page):
                print("Changed to hourly")
            else:
                print("Could not find frequency selector - you may need to do it manually")
        else:
            print("Could not find download button automatically")
            print("Taking screenshot for debugging...")
            page.screenshot(path="kalshi_debug.png")
            print("Saved screenshot to kalshi_debug.png")
        
        print("\n--- INSTRUCTIONS ---")
        print("If the download didn't start automatically:")
        print("1. Look for a download/data/export button on the page")
        print("2. Click it and select hourly frequency")
        print("3. Save the downloaded file to the data folder")
        print("\nPress Enter to close browser...")
        try:
            input()
        except:
            pass
        
        browser.close()


if __name__ == "__main__":
    download_with_headless_false()