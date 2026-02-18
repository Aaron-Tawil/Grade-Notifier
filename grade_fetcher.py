import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GradeFetcher:
    GRADES_URL = "https://my.tau.ac.il/TAU_Student/ExamsAndTasks"
    DATA_ACTION_URL_PART = "DataActionGetExamsAndTasks"

    def __init__(self, headless=True, keep_open=False):
        self.headless = headless
        self.keep_open = keep_open
        self.fetched_data = None
        self.browser = None
        self.context = None
        self.page = None

    def fetch_grades(self):
        """
        Launches browser, logs in, intercepts grade data, and returns it.
        """
        logger.info("Starting GradeFetcher...")
        
        # Manually start Playwright to control lifecycle
        self.playwright = sync_playwright().start()
        p = self.playwright
        
        try:
            self.browser = p.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
            
            page = self.page

            # Set up interception handler
            page.on("response", self._handle_response)

            try:
                logger.info(f"Navigating to {self.GRADES_URL}...")
                page.goto(self.GRADES_URL)

                # Robust loop: Check for login/intro/data repeatedly
                logger.info("Entering robust check loop (max 60s)...")
                start_time = 0
                import time
                start_time = time.time()
                
                while time.time() - start_time < 60:
                    # 1. Check if data captured
                    if self.fetched_data:
                        logger.info("Grade data successfully intercepted.")
                        break

                    # 2. Check for Login
                    self._handle_login(page)

                    # 3. Check for Intro
                    self._handle_intro(page)
                    
                    # 4. Check if we need to reload (e.g. stuck)
                    # For now just wait
                    page.wait_for_timeout(2000)

                if not self.fetched_data:
                    logger.warning("Timed out waiting for grade data.")
                    # Take a screenshot for debugging if failed
                    try: page.screenshot(path="fetch_failure.png")
                    except: pass
                    raise TimeoutError("Timed out waiting for grade data (60s)")

            except Exception as e:
                logger.error(f"Error during fetch: {e}")
                try: page.screenshot(path="fetch_error.png")
                except: pass
                raise e
            
            if not self.keep_open:
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
            
            
            if self.fetched_data:
                 return self.process_grades(self.fetched_data)
            return []

        except Exception as e:
            logger.error(f"Critical error in GradeFetcher: {e}")
            if not self.keep_open:
                self.close()
            raise e # Re-raise to let caller handle notification

    def _handle_response(self, response):
        """
        Callback for network responses. Captures JSON from the target DataAction.
        """
        try:
            if self.DATA_ACTION_URL_PART in response.url and "Filters" not in response.url and response.status == 200:
                logger.info(f"Intercepted target response: {response.url}")
                data = response.json()
                # Basic validation
                if "data" in data and "ExamsAndTasksLis" in data["data"]:
                    self.fetched_data = data["data"]["ExamsAndTasksLis"]["List"]
                    logger.info(f"Captured {len(self.fetched_data)} grade records.")
        except Exception as e:
            # Ignore parsing errors for non-JSON or irrelevant responses
            pass

    def _handle_login(self, page):
        """
        Handles the login flow if redirected to the login page.
        """
        try:
            # Check for Login Page
            if "nidp" in page.url or page.locator("input[name='Ecom_User_ID']").count() > 0 or page.locator("input[name='user_name']").count() > 0 or page.locator("input[name='txtUser']").count() > 0:
                logger.info("Login page detected. Attempting to log in...")
                
                # Standard portal login
                if page.locator("input[name='txtUser']").count() > 0:
                    page.fill("input[name='txtUser']", os.environ["UNI_USER"])
                    page.fill("input[name='txtPass']", os.environ["UNI_PASS"])
                    page.fill("input[name='txtId']", os.environ["UNI_ID"])
                    try: page.check("input[type='checkbox']", timeout=1000)
                    except: pass
                    page.click("button[type='submit']")

                # Alternative login form 1
                elif page.locator("input[name='user_name']").count() > 0:
                    page.fill("input[name='user_name']", os.environ["UNI_USER"])
                    page.fill("input[name='id_number']", os.environ["UNI_ID"])
                    page.fill("input[name='password']", os.environ["UNI_PASS"])
                    
                    submit_clicked = False
                    for selector in ["button[type='submit']", "input[type='submit']", "button:has-text('כניסה')", "button:has-text('Login')"]:
                        if page.locator(selector).count() > 0:
                            try:
                                page.click(selector, timeout=5000)
                                submit_clicked = True
                                break
                            except: pass
                    if not submit_clicked:
                        page.keyboard.press("Enter")
                
                # Alternative login form 2 (Ecom)
                elif page.locator("input[name='Ecom_User_ID']").count() > 0:
                    page.fill("input[name='Ecom_User_ID']", os.environ["UNI_USER"])
                    page.fill("input[name='Ecom_User_Pid']", os.environ["UNI_ID"])
                    page.fill("input[name='Ecom_Password']", os.environ["UNI_PASS"])
                    page.click("button[name='loginButton2']")

                page.wait_for_load_state("networkidle")
                logger.info("Login submitted.")
            else:
                logger.info("No login form detected (already logged in?).")
        except Exception as e:
            logger.warning(f"Login handler exception: {e}")


    def _handle_intro(self, page):
        """
        Bypasses the 'Intro' screen if it appears.
        """
        try:
            # Check for Intro/Welcome page context
            if "IntroScreen" in page.url or page.locator('#IntroContainer').count() > 0 or page.locator("#Skip").count() > 0:
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
                    if page.locator(selector).count() > 0:
                        logger.info(f"Clicking skip selector: {selector}")
                        try:
                            # Use evaluate click which is sometimes more robust against overlays
                            # But standard click is fine if we handle timeouts
                            page.locator(selector).first.click(timeout=3000)
                            bypassed = True
                            page.wait_for_load_state("networkidle", timeout=5000)
                            break
                        except:
                            pass
                 
                 if not bypassed:
                     logger.warning("Could not click any skip button. Attempting to force navigation...")
                     page.goto(self.GRADES_URL)

        except Exception as e:
            logger.warning(f"Intro handler exception: {e}")

    def process_grades(self, raw_data: list) -> list[dict[str, object]]:
        """
        Process raw API data to match the structure expected by main.py
        Returns list of dicts with keys: course, grade, moed, date, term, notebook_available
        """
        processed = []
        dropped_without_course = 0
        for item in raw_data:
            try:
                # Helper to safely get string values
                def get_val(key):
                    return str(item.get(key) or "").strip()

                def looks_like_file_reference(raw: object) -> bool:
                    if raw is None:
                        return False
                    value = str(raw).strip()
                    if not value:
                        return False
                    lowered = value.lower()
                    if lowered in {"-", "--", "none", "null", "false", "0"}:
                        return False
                    return any(token in lowered for token in (".pdf", ".doc", ".docx", ".zip", "download", "/"))

                def has_notebook_file(payload: dict) -> bool:
                    # The API often includes placeholder scan fields.
                    # Use a strict rule to avoid false positives:
                    # 1) status must explicitly indicate file
                    # 2) at least one file reference must look real
                    scan_status = str(payload.get("ScanStatus") or "").strip().lower()
                    if scan_status != "file":
                        return False
                    return looks_like_file_reference(payload.get("File")) or looks_like_file_reference(payload.get("ScanFileName"))

                def normalize_api_date(raw: str) -> str:
                    if not raw:
                        return ""
                    candidate = raw.split("T", 1)[0]
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"):
                        try:
                            dt = datetime.strptime(raw, fmt)
                            return dt.strftime("%Y-%m-%d %H:%M" if "%H" in fmt else "%Y-%m-%d")
                        except ValueError:
                            continue
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                        try:
                            dt = datetime.strptime(candidate, fmt)
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            continue
                    return candidate

                record = {
                    "course": get_val("CourseDescription") or get_val("Course"),
                    "grade": get_val("FinalGrade"),
                    "moed": get_val("DueDescription"),
                    "term": get_val("AssignmentDescription"),
                    "date": normalize_api_date(get_val("DueDate")),
                    "notebook_available": False,
                    "raw_text": "" # Optional, scraper fills it with row text
                }
                
                # Notebook availability logic
                if has_notebook_file(item):
                    record["notebook_available"] = True

                # Keep every row that belongs to a course, even if grade/date is still empty.
                if record["course"]:
                    processed.append(record)
                else:
                    dropped_without_course += 1

            except Exception as e:
                logger.warning(f"Error processing item {item.get('Id')}: {e}")
        if dropped_without_course:
            logger.info(f"Dropped {dropped_without_course} API rows without course name.")
        return processed

    def close(self):
        """Manually close browser and stop Playwright if kept open."""
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright') and self.playwright:
            self.playwright.stop()

if __name__ == "__main__":
    # Test execution
    fetcher = GradeFetcher(headless=False)
    grades = fetcher.fetch_grades()
    if grades:
        print(json.dumps(grades[:2], indent=2, ensure_ascii=False)) # Print first 2 for verification
        print(f"Total grades fetched: {len(grades)}")
    else:
        print("Failed to fetch grades.")
