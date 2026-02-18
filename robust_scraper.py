import os
import sys
import time
import logging
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Iterable
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# Configure logging
logger = logging.getLogger(__name__)

# Constants
GRADES_URL = "https://my.tau.ac.il/TAU_Student/ExamsAndTasks"
TABLE_SELECTOR = "div.tau-Table-container table" 
DESKTOP_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

HEADER_ALIASES: Dict[str, List[str]] = {
    "course": ["\u05e9\u05dd \u05d4\u05e7\u05d5\u05e8\u05e1"], # Shem HaKurs
    "grade": ["\u05e6\u05d9\u05d5\u05df"], # Tziyun
    "moed": ["\u05de\u05d5\u05e2\u05d3"], # Moed
    "date": ["\u05ea\u05d0\u05e8\u05d9\u05da \u05d5\u05e9\u05e2\u05d4"], # Tarich veSha'a
    "term": ["\u05e1\u05d5\u05d2"], # Sug (Type)
}

def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.replace("\xa0", " ").split())

def header_to_key(header_text: str) -> Optional[str]:
    header = normalize_text(header_text)
    if not header:
        return None
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in header:
                return key
    return None

def normalize_date(value: str) -> str:
    clean = normalize_text(value)
    if not clean or clean in {"-", "--"}:
        return ""
    parts = clean.split()
    if not parts:
        return ""
    date_part = parts[0]
    time_part = parts[1] if len(parts) > 1 else ""

    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(" ".join(filter(None, [date_part, time_part])), fmt)
            return dt.strftime("%Y-%m-%d %H:%M" if "%H" in fmt else "%Y-%m-%d")
        except ValueError:
            continue
    return clean

class RobustGradesScraper:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.playwright = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=["--lang=he-IL"] 
        )
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=DESKTOP_USER_AGENT
        )
        self.page = self.context.new_page()
        # self.page.on("console", lambda msg: logger.debug(f"Console: {msg.text}"))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def login(self, username, password, id_num) -> bool:
        """Robust login flow for the new portal."""
        logger.info(f"Navigating to {GRADES_URL}...")
        try:
            self.page.goto(GRADES_URL, wait_until="domcontentloaded", timeout=60000)
            
            # Check if already logged in (Table found)
            try:
                self.page.wait_for_selector(TABLE_SELECTOR, timeout=5000)
                logger.info("Already logged in (Table found).")
                return True
            except:
                pass 

            # Check for Intro/Welcome page and bypass
            if "IntroScreen" in self.page.url or self.page.locator('#IntroContainer').count() > 0:
                logger.info("Intro screen detected. Attempting to bypass...")
                skip_selectors = [
                    "#Skip",
                    "a:has-text('Got it, don\\'t show again')",
                    "a:has-text('לא צריך להראות לי את זה שוב')",
                    "button:has-text('המשך')",
                    "a:has-text('המשך')",
                    "button:has-text('כניסה לאזור האישי')",
                    "a:has-text('כניסה לאזור האישי')",
                ]
                
                bypassed = False
                for selector in skip_selectors:
                    if self.page.locator(selector).count() > 0:
                        logger.info(f"Clicking skip selector: {selector}")
                        self.page.locator(selector).first.click()
                        bypassed = True
                        self.page.wait_for_timeout(1000)
                        break
                
                if not bypassed:
                     logger.warning("Could not find skip button. Forcing navigation...")
                     self.page.goto(GRADES_URL, wait_until="domcontentloaded")

                try:
                    self.page.wait_for_selector(TABLE_SELECTOR, timeout=15000)
                    logger.info("Intro bypassed, table found.")
                    return True
                except:
                     pass

            # Check for Login Page
            if "nidp" in self.page.url or self.page.locator("input[name='Ecom_User_ID']").count() > 0 or self.page.locator("input[name='user_name']").count() > 0:
                logger.info("Login page detected. Attempting to log in...")
                
                if self.page.locator("input[name='user_name']").count() > 0:
                    self.page.fill("input[name='user_name']", username)
                    self.page.fill("input[name='id_number']", id_num)
                    self.page.fill("input[name='password']", password)
                    
                    # Robust submit
                    submit_clicked = False
                    for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('כניסה')", "button:has-text('Login')"]:
                        if self.page.locator(selector).count() > 0:
                            try:
                                self.page.click(selector, timeout=5000)
                                submit_clicked = True
                                break
                            except:
                                pass
                    if not submit_clicked:
                        self.page.keyboard.press("Enter")

                elif self.page.locator("input[name='Ecom_User_ID']").count() > 0:
                    self.page.fill("input[name='Ecom_User_ID']", username)
                    self.page.fill("input[name='Ecom_User_Pid']", id_num)
                    self.page.fill("input[name='Ecom_Password']", password)
                    self.page.click("button[name='loginButton2']")

                # Wait for redirect
                try:
                    self.page.wait_for_url(lambda u: "TAU_Student" in u and "Intro" not in u, timeout=60000)
                    logger.info("Login successful (redirected to portal).")
                    
                    if "IntroScreen" in self.page.url:
                         logger.info("Landing on Intro after login. Recursively calling login...")
                         return self.login(username, password, id_num)

                    if "ExamsAndTasks" not in self.page.url:
                        logger.info("Redirected to Dashboard/Other. Navigating to Exams page...")
                        self.page.goto(GRADES_URL, wait_until="domcontentloaded")
                    
                    return True
                except PWTimeout:
                    logger.error("Timeout waiting for redirect after login.")
                    return False

            return False

        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    def scrape(self) -> List[Dict[str, Any]]:
        """Clears filters and scrapes the table, returning structured data."""
        logger.info("Starting scrape process (Clear Filters + Extract)...")
        try:
            self.page.wait_for_selector(TABLE_SELECTOR, timeout=30000)
            
            # Clear filters with robust retry
            remove_button_selector = ".vscomp-value-tag-clear-button"
            max_loops = 5
            
            for i in range(max_loops):
                buttons = self.page.locator(remove_button_selector)
                count = buttons.count()
                
                if count == 0:
                    break
                
                logger.debug(f"Found {count} filters to clear (Round {i+1})...")
                
                try:
                    buttons.first.click()
                    self.page.wait_for_timeout(2000) # Wait for table refresh (Increased to 2s)
                except Exception as e:
                    logger.warning(f"Failed to click filter button: {e}")
            
            self.page.wait_for_load_state("domcontentloaded")
            self.page.wait_for_timeout(1000)

            # Locate headers
            # Note: OutSystems headers might be in 'thead th'
            # We need to map column index to header key
            headers = []
            header_cells = self.page.locator(f"{TABLE_SELECTOR} thead th").all()
            if not header_cells:
                 # Try first row if no thead
                 header_cells = self.page.locator(f"{TABLE_SELECTOR} tr").first.locator("th, td").all()
            
            for cell in header_cells:
                headers.append(header_to_key(cell.inner_text()))
            
            logger.debug(f"Mapped headers: {headers}")

            # Extract rows
            extracted_records = []
            rows = self.page.locator(f"{TABLE_SELECTOR} tbody tr").all()
            
            for row in rows:
                if not row.is_visible():
                    continue
                    
                cells = row.locator("td").all()
                if not cells: 
                    continue

                record: Dict[str, Any] = {
                    "course": "", "grade": "", "moed": "", "term": "", "date": "",
                    "notebook_available": False, "raw_text": ""
                }
                
                 # Check notebook button
                notebook_btn = row.locator("button.icon-ShowNote, button:has-text('הצגת'), button:has-text('מחברת')")
                if notebook_btn.count() > 0:
                     is_disabled = notebook_btn.first.is_disabled()
                     
                     if not is_disabled:
                        # Some buttons seem to have 'empty-btn' but are still clickable/enabled.
                        # Trusting is_disabled() as primary indicator.
                        record["notebook_available"] = True
                        # logger.debug(f"*** Notebook FOUND for {record['course']} ***")
                     else:
                        # logger.debug(f"Notebook unavailable for {record['course']}")
                        pass

                raw_text = row.inner_text()
                record["raw_text"] = normalize_text(raw_text)

                for i, cell in enumerate(cells):
                    if i < len(headers) and headers[i]:
                        key = headers[i]
                        val = normalize_text(cell.inner_text())
                        if key == "date":
                            val = normalize_date(val)
                        record[key] = val
                
                # Keep every visible course row, even if grade/date is still empty.
                if record["course"]:
                    extracted_records.append(record)
            
            logger.info(f"Extracted {len(extracted_records)} records.")
            
            if len(extracted_records) == 0:
                 logger.warning("Extracted 0 records. Saving debug state...")
                 self.page.screenshot(path="debug_0_records.png")
                 with open("debug_0_records.html", "w", encoding="utf-8") as f:
                     f.write(self.page.content())
            
            return extracted_records

        except Exception as e:
            logger.error(f"Error during scrape: {e}")
            raise e
