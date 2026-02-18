from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
import re
import logging
from typing import Any, Dict, Iterable, List, Optional

import urllib3
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from dataclasses import asdict
from ims import IMS, GradeInfo

# Suppress the InsecureRequestWarning from urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover - optional for local testing
    storage = None  # type: ignore

PORTAL_LOGIN_URL = "https://my.tau.ac.il/TAU_Student/ExamsAndTasks"
GRADES_URL = "https://my.tau.ac.il/TAU_Student/ExamsAndTasks"

TABLE_SELECTOR = "#b22-Table"
FALLBACK_TABLE = 'table.table[role="grid"]'

HEADER_ALIASES: Dict[str, List[str]] = {
    "course": ["\u05e9\u05dd \u05d4\u05e7\u05d5\u05e8\u05e1"],
    "grade": ["\u05e6\u05d9\u05d5\u05df"],
    "moed": ["\u05de\u05d5\u05e2\u05d3"],
    "date": ["\u05ea\u05d0\u05e8\u05d9\u05da \u05d5\u05e9\u05e2\u05d4"],
    "term": ["\u05e1\u05d5\u05d2"],
}

TEMP_ROOT = Path(os.getenv("GRADE_NOTIFIER_TEMP", tempfile.gettempdir()))
OUT_DIR = Path(os.getenv("GRADE_DEBUG_DIR", TEMP_ROOT / "tau_grades_out"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = Path(os.getenv("PLAYWRIGHT_PROFILE_DIR", TEMP_ROOT / "tau_profile"))
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

ARTIFACT_PREFIX = os.getenv("GCS_ARTIFACT_PREFIX", "tau_grades/debug")
CACHE_FILE_NAME = os.getenv("CACHE_FILE_NAME", "grades_cache.json")
IMS_CACHE_FILE_NAME = os.getenv("IMS_CACHE_FILE_NAME", "grades_cache_ims.json")


def _resolve_ims_verify_setting() -> tuple[bool | str, bool]:
    """Determine how the IMS client should verify TLS certificates."""
    ca_bundle = os.getenv("IMS_CA_BUNDLE", "").strip()
    if ca_bundle:
        bundle_path = Path(ca_bundle)
        if bundle_path.exists():
            return str(bundle_path), True
        logger.warning(f"IMS_CA_BUNDLE path '{bundle_path}' not found; falling back to IMS_VERIFY_SSL flag.")
    verify_flag = _is_truthy(os.getenv("IMS_VERIFY_SSL", "true"))
    return verify_flag, verify_flag

load_dotenv()

UNI_USER = os.getenv("UNI_USER", "")
UNI_PASS = os.getenv("UNI_PASS", "")
UNI_ID = os.getenv("UNI_ID", "")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")


def _is_truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"} if value else False


USE_PERSISTENT_CONTEXT = _is_truthy(os.getenv("USE_PERSISTENT_CONTEXT", ""))
HEADLESS_DEFAULT = not _is_truthy(os.getenv("RUN_HEADFUL", ""))

DESKTOP_VIEWPORT = {
    "width": int(os.getenv("PLAYWRIGHT_VIEWPORT_WIDTH", "1600")),
    "height": int(os.getenv("PLAYWRIGHT_VIEWPORT_HEIGHT", "900")),
}
DESKTOP_USER_AGENT = os.getenv(
    "PLAYWRIGHT_DESKTOP_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
)


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


def parse_grade_row(
    cells: Iterable[Dict[str, str]],
    *,
    raw_text: str = "",
    notebook_available: bool = False,
) -> Dict[str, str]:
    record: Dict[str, str] = {
        "course": "",
        "grade": "",
        "moed": "",
        "term": "",
        "date": "",
    }
    for cell in cells:
        key = header_to_key(cell.get("header", ""))
        if not key:
            continue
        value = normalize_text(cell.get("text", ""))
        if key == "date":
            value = normalize_date(value)
        record[key] = value

    record["notebook_available"] = notebook_available
    record["raw_text"] = normalize_text(raw_text)
    return record


def parse_grade_table_html(html: str) -> List[Dict[str, str]]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover - only when tests forget dependency
        raise RuntimeError("BeautifulSoup is required to parse HTML outside the browser") from exc

    soup = BeautifulSoup(html, "html.parser")
    rows: List[Dict[str, str]] = []
    for tr in soup.select("tbody tr"):
        cells = [
            {
                "header": (td.get("data-header") or "").strip(),
                "text": td.get_text(strip=True),
            }
            for td in tr.find_all("td")
        ]
        if not cells:
            continue
        rows.append(parse_grade_row(cells, raw_text=tr.get_text(" ", strip=True)))
    return rows


def extract_from_table(table_handle) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    row_handles = table_handle.query_selector_all("tbody tr")
    for row in row_handles:
        cell_handles = row.query_selector_all("td")
        if not cell_handles:
            continue
        cells = [
            {
                "header": normalize_text(cell.get_attribute("data-header")),
                "text": normalize_text(cell.inner_text()),
            }
            for cell in cell_handles
        ]
        notebook_button = row.query_selector("button[data-button], button.icon-ShowNote, button")
        notebook_available = False
        if notebook_button:
            button_text = notebook_button.inner_text()
            normalized_button_text = normalize_text(button_text)
            notebook_available = (
                notebook_button.get_attribute("disabled") is None
                and "הצגת" in normalized_button_text
            )
        records.append(
            parse_grade_row(
                cells,
                raw_text=row.inner_text(),
                notebook_available=notebook_available,
            )
        )
    return [rec for rec in records if any(rec.get(part) for part in ("course", "grade"))]


def extract_exam_details(page) -> List[Dict[str, str]]:
    table = page.query_selector(f"{TABLE_SELECTOR} table")
    if not table:
        table = page.query_selector(TABLE_SELECTOR)
    if not table:
        table = page.query_selector(FALLBACK_TABLE)

    if table:
        records = extract_from_table(table)
        if records:
            return records

    logger.warning("No grade table detected; returning empty result.")
    return []


def canonicalize(records: Iterable[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    for rec in records:
        course = (rec.get("course") or "").strip()
        moed = (rec.get("moed") or "").strip()
        date = (rec.get("date") or "").strip()
        grade = (rec.get("grade") or "").strip()
        term = (rec.get("term") or "").strip()

        base_key = course or rec.get("raw_text") or f"row_{len(result)}"
        if moed:
            base_key = f"{base_key} | moed {moed}"

        key = base_key
        if key in result:
            if date and result[key].get("date") != date:
                key = f"{base_key} | {date}"
            else:
                suffix = 2
                while f"{base_key} ({suffix})" in result:
                    suffix += 1
                key = f"{base_key} ({suffix})"

        result[key] = {
            "course": course,
            "grade": grade,
            "term": term,
            "moed": moed,
            "date": date,
            "notebook_available": "true" if _is_truthy(rec.get("notebook_available")) else "false",
            "raw_text": rec.get("raw_text", ""),
        }
    return result


def print_preview(current: Dict[str, Dict[str, str]]) -> None:
    logger.info("\n=== Parsed grades preview ===")
    for key, data in current.items():
        title = data.get("course") or key
        grade = data.get("grade", "")
        moed = data.get("moed", "")
        term = data.get("term", "")
        date = data.get("date", "")
        extras = [
            f"Moed: {moed}" if moed else "",
            f"Term: {term}" if term else "",
            f"Date: {date}" if date else "",
        ]
        extras = [item for item in extras if item]
        suffix = f"  |  {'  '.join(extras)}" if extras else ""
        logger.info(f"{title}  |  Grade: {grade}{suffix}")
    logger.info("=== end ===\n")


def save_debug_to_gcs(page, tag: str = "debug") -> None:
    html_path = OUT_DIR / f"{tag}.html"
    png_path = OUT_DIR / f"{tag}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as exc:
        logger.error(f"Error writing local debug artifacts: {exc}")

    if not GCS_BUCKET_NAME or storage is None:
        return
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        bucket.blob(f"{ARTIFACT_PREFIX}/{tag}.html").upload_from_filename(str(html_path))
        bucket.blob(f"{ARTIFACT_PREFIX}/{tag}.png").upload_from_filename(str(png_path))
    except Exception as exc:  # pragma: no cover - network access
        logger.error(f"Error saving debug artifacts to GCS: {exc}")


def load_cache_from_gcs(cache_file: str) -> Any:
    if not GCS_BUCKET_NAME or storage is None:
        return {}
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(cache_file)
        if blob.exists():
            cache_content = blob.download_as_text()
            return json.loads(cache_content)
    except Exception as exc:  # pragma: no cover - network access
        logger.error(f"Error loading cache from GCS ({cache_file}): {exc}")
    return {}


def save_cache_to_gcs(data: Any, cache_file: str) -> None:
    if not GCS_BUCKET_NAME or storage is None:
        return
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(cache_file)
        blob.upload_from_string(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as exc:  # pragma: no cover - network access
        logger.error(f"Error saving cache to GCS ({cache_file}): {exc}")


def get_changes(current: Dict[str, Dict[str, str]], previous: Dict[str, Dict[str, str]]) -> Dict[str, tuple[Optional[Dict[str, str]], Dict[str, str]]]:
    """Compares two dictionaries and returns a dict of changes.

    The value for each change is a tuple: (previous_state, current_state).
    For new items, previous_state will be None.
    """
    changes: Dict[str, tuple[Optional[Dict[str, str]], Dict[str, str]]] = {}
    for key, current_value in current.items():
        previous_value = previous.get(key)
        if previous_value != current_value:
            changes[key] = (previous_value, current_value)
    return changes

def _send_telegram_message(message: str, parse_mode: str = "Markdown") -> None:
    """Sends a message via Telegram."""
    import requests

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not found. Skipping notification.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("--- Telegram Notification Sent Successfully! ---")
        else:
            logger.error("--- Failed to Send Telegram Notification ---")
            logger.error(f"Status Code: {response.status_code}")
            logger.error(f"Response: {response.text}")
    except Exception as e:
        logger.error(f"--- An error occurred while sending Telegram notification: {e} ---")


def send_notification(changes: Dict[str, tuple[Optional[Dict[str, str]], Dict[str, str]]]) -> None:
    """Formats the grade changes and sends a notification message via Telegram."""
    message_lines = ["🔔 *Grade Update!* 🔔"]
    for key, (previous_value, current_value) in changes.items():
        course_name = current_value.get("course") or key

        # Case 1: It's a brand new grade entry
        if previous_value is None:
            grade = current_value.get("grade", "N/A")
            message_lines.append(f"• *{course_name}* (New): {grade}")
            continue

        # Case 2: It's an update to an existing entry
        change_details = []
        old_grade = previous_value.get("grade", "N/A")
        new_grade = current_value.get("grade", "N/A")
        if old_grade != new_grade:
            change_details.append(f"Grade changed from `{old_grade}` to *{new_grade}*")

        old_notebook = previous_value.get("notebook_available", "false")
        new_notebook = current_value.get("notebook_available", "false")
        if old_notebook != new_notebook:
            status = "now available" if new_notebook == "true" else "no longer available"
            change_details.append(f"Notebook is {status}")

        if change_details:
            message_lines.append(f"• *{course_name}*: {', '.join(change_details)}")
        else:
            # Fallback for when a change is detected but not in the fields we track
            message_lines.append(f"• *{course_name}*: Updated (Grade: {new_grade})")

    message = "\n".join(message_lines)
    _send_telegram_message(message)


def fetch_course_names() -> dict[str, str]:
    """Load the local catalog mapping course IDs to names."""
    try:
        with open("data/courses.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.warning("Warning: 'data/courses.json' not found. Course names will be unknown.")
        return {}
    except json.JSONDecodeError:
        logger.warning("Warning: Failed to decode JSON from 'data/courses.json'. Course names will be unknown.")
        return {}

    mapping: dict[str, str] = {}
    for course_id, info in data.items():
        if isinstance(info, dict) and "name" in info:
            mapping[course_id.replace("-", "")] = info["name"]
    return mapping


def get_ims_changes(current_grades: List[GradeInfo], previous_grades: List[GradeInfo]) -> List[tuple[Optional[GradeInfo], GradeInfo]]:
    """Compares two lists of GradeInfo objects and returns a list of changes."""
    changes: List[tuple[Optional[GradeInfo], GradeInfo]] = []
    
    prev_map = {f"{g.course_id}-{g.semester}": g for g in previous_grades}
    curr_map = {f"{g.course_id}-{g.semester}": g for g in current_grades}

    for key, current_grade in curr_map.items():
        previous_grade = prev_map.get(key)
        if previous_grade != current_grade:
            changes.append((previous_grade, current_grade))
            
    return changes


def send_ims_notification(changes: List[tuple[Optional[GradeInfo], GradeInfo]], course_names: Dict[str, str]) -> None:
    """Formats the IMS grade changes and sends a notification."""
    message_lines = ["🔔 *Grade Update (IMS API)!* 🔔"]

    for prev, current in changes:
        course_name = course_names.get(current.course_id, current.course_id)
        
        if prev is None:
            # New grade
            grade_display = "Exempt" if current.is_exempt else (str(current.grade) if current.grade is not None else "N/A")
            message_lines.append(f"• *{course_name}* ({current.semester.upper()}) (New): {grade_display}")
        else:
            # Modified grade
            change_details = []
            
            old_grade_display = "Exempt" if prev.is_exempt else (str(prev.grade) if prev.grade is not None else "N/A")
            new_grade_display = "Exempt" if current.is_exempt else (str(current.grade) if current.grade is not None else "N/A")
            
            if old_grade_display != new_grade_display:
                change_details.append(f"Grade changed from `{old_grade_display}` to *{new_grade_display}*")
            elif prev.is_exempt != current.is_exempt:
                 status = "Exempt" if current.is_exempt else "No longer exempt"
                 change_details.append(f"Status changed to: *{status}*")

            if change_details:
                message_lines.append(f"• *{course_name}* ({current.semester.upper()}): {', '.join(change_details)}")
            else:
                # Fallback for other changes
                message_lines.append(f"• *{course_name}* ({current.semester.upper()}): Updated")

    message = "\n".join(message_lines)
    _send_telegram_message(message)





def bypass_intro(page) -> None:
    """Skip the TAU intro screen when it appears."""
    try:
        page.wait_for_timeout(500)
        intro = page.locator('#IntroContainer')
        intro_visible = intro.count() and intro.first.is_visible()

        skip_clicked = False
        skip_selectors = [
            "#Skip",
            "a:has-text('\u05dc\u05d0 \u05e6\u05e8\u05d9\u05da \u05dc\u05d4\u05e8\u05d0\u05d5\u05ea \u05dc\u05d9 \u05d0\u05ea \u05d6\u05d4 \u05e9\u05d5\u05d1')",
            "text='\u05dc\u05d0 \u05e6\u05e8\u05d9\u05da \u05dc\u05d4\u05e8\u05d0\u05d5\u05ea \u05dc\u05d9 \u05d0\u05ea \u05d6\u05d4 \u05e9\u05d5\u05d1'",
        ]
        for selector in skip_selectors:
            target = page.locator(selector)
            if target.count():
                try:
                    target.first.click(timeout=3000)
                    page.wait_for_timeout(200)
                    skip_clicked = True
                    break
                except Exception:
                    continue

        action_clicked = False
        action_selectors = [
            "button:has-text('\u05d4\u05de\u05e9\u05da')",
            "a:has-text('\u05d4\u05de\u05e9\u05da')",
            "button:has-text('\u05db\u05e0\u05d9\u05e1\u05d4 \u05dc\u05d0\u05d6\u05d5\u05e8 \u05d4\u05d0\u05d9\u05e9\u05d9')",
            "a:has-text('\u05db\u05e0\u05d9\u05e1\u05d4 \u05dc\u05d0\u05d6\u05d5\u05e8 \u05d4\u05d0\u05d9\u05e9\u05d9')",
        ]
        for selector in action_selectors:
            target = page.locator(selector)
            if target.count():
                try:
                    target.first.click(timeout=3000)
                    action_clicked = True
                    break
                except Exception:
                    continue

        if (intro_visible or skip_clicked or action_clicked) and intro.count() and intro.first.is_visible():
            page.evaluate(
                """
                () => {
                    try {
                        localStorage.setItem('SkipIntro', 'true');
                        localStorage.setItem('IntroSeen', 'true');
                        localStorage.setItem('DontShowIntro', 'true');
                    } catch (e) {
                        console.warn('Intro bypass storage error', e);
                    }
                }
                """
            )
            page.reload(wait_until='networkidle', timeout=30000)
    except Exception:
        pass




def taunidp_login(page, user: str, pwd: str, national_id: str = "", max_wait_ms: int = 90000) -> bool:
    """Log in on the TAU NIDP React screen."""
    logger.info("[login] logging in")
    page.wait_for_load_state("domcontentloaded", timeout=max_wait_ms)
    page.wait_for_function(
        """
        () => !!document.querySelector('input[type="password"]') ||
              !!document.querySelector('button, input[type="submit"]')
        """,
        timeout=max_wait_ms,
    )
    logger.debug("searching username input")

    user_loc = None
    user_selector_used = None
    for selector in (
        "input[name='user_name']",
        "input[autocomplete='username']",
        "input[type='email']",
        "input[type='text']",
    ):
        candidate = page.locator(selector)
        logger.debug(f"Candidate: {candidate}")
        if candidate.count():
            user_loc = candidate.first
            user_selector_used = selector
            break

    id_loc = None
    id_selector_used = None
    if national_id:
        for selector in (
            "input[name='id_number']",
            "input[name*='id']",
            "input[data-testid*='id']",
            "input[type='text']",
        ):
            candidate = page.locator(selector)
            if selector == "input[type='text']":
                if candidate.count() >= 2:
                    id_loc = candidate.nth(1)
                    id_selector_used = f"{selector}#1"
                    break
            elif candidate.count():
                id_loc = candidate.first
                id_selector_used = selector
                break

    pass_candidates = page.locator('input[type="password"]')
    pass_loc = pass_candidates.first if pass_candidates.count() else None
    logger.debug(f"[login] user selector: {user_selector_used}, id selector: {id_selector_used}, pass available: {bool(pass_loc)}")

    if user_loc:
        try:
            user_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        logger.debug("[login] user locator found")
        user_loc.fill(user)
    else:
        logger.warning("[login] user locator not found")

    if national_id and id_loc:
        try:
            id_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        id_loc.fill(national_id)
    elif national_id:
        logger.warning("[login] ID locator not found")

    if pass_loc:
        try:
            pass_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        pass_loc.fill(pwd)
    else:
        logger.warning("[login] password locator not found")

    submit = None
    button_locator = page.get_by_role(
        "button",
        name=re.compile("(\u05db\u05e0\u05d9\u05e1\u05d4|\u05d4\u05ea\u05d7\u05d1\u05e8)"),
        exact=False,
    )
    if button_locator.count():
        submit = button_locator.first
    elif page.locator('button[type="submit"]').count():
        submit = page.locator('button[type="submit"]').first
    elif page.locator('input[type="submit"]').count():
        submit = page.locator('input[type="submit"]').first

    if submit:
        try:
            submit.click()
        except Exception:
            try:
                submit.click(force=True)
            except Exception:
                page.keyboard.press("Enter")
    else:
        page.keyboard.press("Enter")

    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except PWTimeout:
        pass

    return True


def apply_default_filters(page) -> None:
    """Clear default calendar filters so all grades are visible."""
    try:
        # The correct selector for the 'x' button, found via inspection.
        # The element is a <span> with role="button".
        remove_button_selector = ".vscomp-value-tag-clear-button"
        
        # Run the clearing logic multiple times to handle lazy loading/re-appearing filters
        # The user noted there are usually 2 filters, and they might load slowly.
        for round_idx in range(3):
            logger.debug(f"Filter clearing round {round_idx + 1}/3...")
            
            # Initial wait for this round to let elements settle/appear
            page.wait_for_timeout(2000)

            # Inner loop: clear all currently visible buttons
            # We limit this to avoid infinite loops if a button is unclickable
            for _ in range(5): 
                # Re-query every time to avoid stale elements
                remove_buttons = page.locator(remove_button_selector)
                count = remove_buttons.count()
                
                if count == 0:
                    logger.debug("No filter remove buttons found in this check.")
                    break
                    
                logger.debug(f"Found {count} filter remove buttons. Clicking the first one...")
                try:
                    # Click the first one
                    remove_buttons.first.click(timeout=5000)
                    # Wait for the UI to react (table refresh)
                    page.wait_for_load_state("networkidle", timeout=5000)
                    page.wait_for_timeout(500) 
                except Exception as e:
                    logger.warning(f"Error clicking filter button: {e}")
                    page.wait_for_timeout(1000)
            
            # After a round of clearing, we loop back to wait and check again
            # in case new filters appeared or the table refreshed with defaults.

        # Final check/wait
        try:
            page.wait_for_load_state("networkidle", timeout=45000)
            logger.debug("Network is idle after filter clearing rounds.")
        except PWTimeout:
            logger.warning("Timeout waiting for network idle after clearing filters.")

    except Exception as exc:
        logger.error(f"An error occurred during filter clearing: {exc}")


def monitor_legacy_playwright() -> None:
    if not UNI_USER or not UNI_PASS:
        logger.error("Set UNI_USER and UNI_PASS environment variables before running.")
        sys.exit(1)

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            context_kwargs = {
                "viewport": DESKTOP_VIEWPORT,
                "user_agent": DESKTOP_USER_AGENT,
                #"locale": "he-IL",
            }
            if USE_PERSISTENT_CONTEXT:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE_DIR),
                    headless=HEADLESS_DEFAULT,
                    **context_kwargs,
                )
            else:
                browser = playwright.chromium.launch(
                    headless=HEADLESS_DEFAULT,
                    args=["--lang=he-IL"],
                )
                context = browser.new_context(**context_kwargs)

            page = context.new_page()
            # page.goto(PORTAL_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            # Retry logic for navigation
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"Navigating to grades URL (Attempt {attempt + 1}/{max_retries})...")
                    # Relaxed wait condition to domcontentloaded to avoid timeouts on network idle
                    page.goto(GRADES_URL, wait_until="domcontentloaded", timeout=60000)
                    
                    # Explicitly wait for something meaningful instead of generic network idle
                    # We wait for either the table or the login inputs to appear
                    try:
                        page.wait_for_function(
                            f"""
                            () => !!document.querySelector('{TABLE_SELECTOR}') || 
                                  !!document.querySelector('input[name="user_name"]') ||
                                  !!document.querySelector('#IntroContainer')
                            """,
                            timeout=30000
                        )
                    except PWTimeout:
                        logger.warning("Warning: Timeout waiting for initial page elements. Proceeding anyway...")

                    break # Success
                except Exception as e:
                    logger.warning(f"Navigation failed on attempt {attempt + 1}: {e}")
                    if attempt == max_retries - 1:
                        raise e
                    page.wait_for_timeout(5000) # Wait before retry

            bypass_intro(page)
            
            # We still want to wait for network idle if possible, but don't fail hard on it
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            
            bypass_intro(page)
            


            login_selectors = {
                "user": "input[name='user_name']",
                "id": "input[name='id_number']",
                "password": "input[name='password']",
            }
            selector_counts = {key: page.locator(sel).count() for key, sel in login_selectors.items()}
            need_login = any(count > 0 for count in selector_counts.values())
            url_login = "nidp" in page.url or "edp_login" in page.url
            logger.debug(f"[login] url={page.url} need_login={need_login} selector_counts={selector_counts} url_login={url_login}")

            if need_login or url_login or True:
                logger.info("[login] entering login flow")
                taunidp_login(page, UNI_USER, UNI_PASS, UNI_ID)
                try:
                    page.wait_for_load_state("networkidle", timeout=60000)
                except PWTimeout:
                    pass
                
                logger.info("Navigating to grades URL after login...")
                try:
                    page.goto(GRADES_URL, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    if "net::ERR_ABORTED" in str(e):
                        logger.debug(f"Navigation warning (handled, likely benign): {e}")
                    else:
                        logger.warning(f"Navigation warning (handled): {e}")

                try:
                    page.wait_for_function(
                        f"() => !!document.querySelector('{TABLE_SELECTOR}')",
                        timeout=30000
                    )
                except PWTimeout:
                    logger.warning("Warning: Timeout waiting for table after login. Proceeding...")
            else:
                logger.info("[login] already authenticated")
                # Same here: relax wait condition
                try:
                    page.goto(GRADES_URL, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    if "net::ERR_ABORTED" in str(e):
                        logger.debug(f"Navigation warning (handled, likely benign): {e}")
                    else:
                        logger.warning(f"Navigation warning (handled): {e}")

                try:
                    page.wait_for_function(
                        f"() => !!document.querySelector('{TABLE_SELECTOR}')",
                        timeout=30000
                    )
                except PWTimeout:
                    logger.warning("Warning: Timeout waiting for table (already auth). Proceeding...")

            # bypass_intro(page)

            # Check for English interface and switch to Hebrew if needed
            try:
                # Look for the user menu button
                user_menu_btn = page.locator("div.username-badge")
                if user_menu_btn.count() > 0:
                    # Check if we are in English mode (e.g. "My Grades" exists or similar English text)
                    # Or simply check if the "עברית" option is available in the menu
                    logger.debug("[language] Checking language settings...")
                    user_menu_btn.first.click()
                    page.wait_for_timeout(1000) # Wait for menu animation
                    
                    # Look for Hebrew option in the menu
                    hebrew_option = page.locator("span:has-text('עברית')")
                    if hebrew_option.count() > 0 and hebrew_option.first.is_visible():
                        logger.info("[language] Found Hebrew option, switching language...")
                        hebrew_option.first.click()
                        page.wait_for_load_state("networkidle", timeout=60000)
                    else:
                        logger.info("[language] Hebrew option not found or already in Hebrew.")
                        # Close menu if it was opened and no action taken
                        page.keyboard.press("Escape")
                else:
                     logger.warning("[language] User menu button not found.")

            except Exception as e:
                logger.warning(f"[language] Error checking/switching language: {e}")

            logger.info("Waiting for page to stabilize...")
            page.wait_for_timeout(2000)
            apply_default_filters(page)
            try:
                page.wait_for_selector(TABLE_SELECTOR, timeout=45000)
            except PWTimeout:
                logger.warning("Grade table selector not found within timeout.")
            exams = extract_exam_details(page)
            logger.info(f"Found {len(exams)} grades via Playwright.")
            if not exams:
                save_debug_to_gcs(page, tag="no_rows")
                _send_telegram_message(
                    "🟡 Grade Notifier Alert 🟡\n\n"
                    "Scraping finished, but no grade table was found.\n\n"
                    "This could be due to a login failure or a change in the website layout. "
                    "A debug artifact has been saved to GCS (if configured).",
                    parse_mode="Markdown"
                )

            current_dict = canonicalize(exams)
            #print_preview(current_dict)

            previous = load_cache_from_gcs(CACHE_FILE_NAME)
            
            # Check for potential data loss (fewer records than cache)
            if len(current_dict) < len(previous):
                 logger.warning(f"Fetched {len(current_dict)} records, but cache has {len(previous)}. Sending warning.")
                 _send_telegram_message(
                    f"⚠️ *Possible Data Loss Warning* ⚠️\n\n"
                    f"Fetched {len(current_dict)} records, but cache has {len(previous)} records.\n"
                    f"This might indicate a scraping failure or partial load.",
                    parse_mode="Markdown"
                 )
            changes = get_changes(current=current_dict, previous=previous)

            if changes:
                logger.info(f"{len(changes)} changes detected compared to cache.")
                save_cache_to_gcs(current_dict, CACHE_FILE_NAME)
                send_notification(changes)

                # Trigger MacroDroid webhook
                macrodroid_url = os.getenv("MACRODROID_WEBHOOK_URL")
                if macrodroid_url:
                    try:
                        import requests
                        logger.info("Triggering MacroDroid webhook...")
                        requests.get(macrodroid_url, timeout=5)
                        logger.info("MacroDroid webhook triggered successfully.")
                    except Exception as e:
                        logger.error(f"Failed to trigger MacroDroid webhook: {e}")
            else:
                logger.info("No changes vs cache.")
        finally:
            if context:
                context.close()
            if browser:
                browser.close()


def monitor_with_ims() -> None:
    """Fetches grades using the IMS API and notifies of changes."""
    if not all([UNI_USER, UNI_ID, UNI_PASS]):
        logger.warning("IMS credentials (UNI_USER, UNI_ID, UNI_PASS) not set. Skipping.")
        return

    logger.info("Fetching grades via IMS API...")
    from requests import exceptions as requests_exceptions

    verify_setting, verify_enabled = _resolve_ims_verify_setting()
    fallback_used = False

    try:
        ims = IMS(username=UNI_USER, id=UNI_ID, password=UNI_PASS, verify_ssl=verify_setting)
    except (requests_exceptions.SSLError, OSError) as exc:
        if verify_enabled:
            logger.warning(f"IMS SSL verification failed ({exc}). Retrying with verification disabled.")
            verify_enabled = False
            fallback_used = True
            ims = IMS(username=UNI_USER, id=UNI_ID, password=UNI_PASS, verify_ssl=False)
        else:
            raise
    
    # Fetch for current and surrounding years for safety
    current_year = datetime.now().year
    years_to_fetch = [current_year - 1, current_year, current_year + 1, current_year + 2]
    
    current_grades = ims.get_all_grades(years=years_to_fetch)
    
    # Sort for consistency
    current_grades.sort(key=lambda g: (g.semester, g.course_id))
    
    logger.info(f"Found {len(current_grades)} grades via IMS.")
    if not current_grades:
        logger.warning("No grades found via IMS. Sending notification and skipping.")
        _send_telegram_message(
            "🟡 Grade Notifier Alert 🟡\n\n"
            "IMS API fetch finished, but no grades were found.\n\n"
            "This could be due to an issue with the API or credentials. "
            "It might also be correct if no grades are currently available.",
            parse_mode="Markdown",
        )
        return

    # Load previous state from cache
    previous_grades_raw = load_cache_from_gcs(IMS_CACHE_FILE_NAME)
    previous_grades = [GradeInfo(**g) for g in previous_grades_raw] if previous_grades_raw else []

    # Detect changes
    changes = get_ims_changes(current_grades, previous_grades)

    if changes:
        logger.info(f"{len(changes)} changes detected in IMS grades.")
        
        # Fetch course names for user-friendly notifications
        course_names = fetch_course_names()
        
        # Send notification
        send_ims_notification(changes, course_names)
        
        # Save new state to cache
        # Convert list of dataclasses to list of dicts for JSON serialization
        save_cache_to_gcs([asdict(g) for g in current_grades], IMS_CACHE_FILE_NAME)

        # Trigger MacroDroid webhook
        macrodroid_url = os.getenv("MACRODROID_WEBHOOK_URL")
        if macrodroid_url:
            try:
                import requests
                logger.info("Triggering MacroDroid webhook...")
                requests.get(macrodroid_url, timeout=30)
                logger.info("MacroDroid webhook triggered successfully.")
            except Exception as e:
                logger.error(f"Failed to trigger MacroDroid webhook: {e}")
    else:
        logger.info("No changes in IMS grades vs cache.")

    if fallback_used:
        warning = (
            "🟡 Grade Notifier Warning 🟡\n\n"
            "IMS monitor had to disable SSL verification after the initial attempt failed.\n"
            "Please refresh the IMS_CA_BUNDLE or trust store to restore full TLS verification."
        )
        logger.warning("IMS monitor completed with SSL verification disabled after fallback.")
        _send_telegram_message(warning)


from robust_scraper import RobustGradesScraper
from grade_fetcher import GradeFetcher

def monitor_grades_with_fallback() -> None:
    """Fetches grades using GradeFetcher (API), falling back to RobustGradesScraper (DOM)."""
    logger.info("Starting Grade Monitor (API + Fallback)...")
    
    exams = []
    fetch_source = "None"
    
    # 1. Try GradeFetcher (API Interception)
    try:
        logger.info("Attempting fetch with GradeFetcher (API)...")
        fetcher = GradeFetcher(headless=HEADLESS_DEFAULT)
        exams = fetcher.fetch_grades()
        if exams:
            fetch_source = "API"
            logger.info(f"GradeFetcher returned {len(exams)} records.")
        else:
            logger.warning("GradeFetcher returned 0 records.")
    except Exception as e:
        logger.error(f"GradeFetcher failed: {e}")
        _send_telegram_message(f"⚠️ GradeFetcher (API) failed: {e}\nAttempting fallback to DOM scraper...")
        # Continue to fallback
        
    # 2. Fallback to RobustGradesScraper (DOM Scraping) if needed
    if not exams:
        logger.info("Falling back to RobustGradesScraper (DOM)...")
        try:
            with RobustGradesScraper(headless=HEADLESS_DEFAULT) as scraper:
                if scraper.login(UNI_USER, UNI_PASS, UNI_ID):
                    exams = scraper.scrape()
                    if exams:
                        fetch_source = "DOM"
                        logger.info(f"RobustGradesScraper returned {len(exams)} records.")
                else:
                    logger.warning("Robust login failed.")
                    # If both failed, we might raise an exception or just return if we want to keep running other monitors
                    # But the caller expects exceptions for critical failures
        except Exception as e:
            logger.error(f"RobustGradesScraper failed: {e}")
            # If both failed, re-raise to notify user
            raise Exception(f"Both API and DOM fetch methods failed. Last error: {e}")

    if not exams:
        # If we still have no exams, check cache to see if this is an anomaly
        previous = load_cache_from_gcs(CACHE_FILE_NAME)
        if previous and len(previous) > 5: # Arbitrary threshold for "established student"
             msg = f"Critical: No grades fetched from either source, but cache has {len(previous)} records."
             logger.error(msg)
             raise Exception(msg)
        else:
             logger.info("No grades found, and cache is empty/small. Assuming new student or no grades yet.")
             return

    # 3. Process Grades (Canonicalize & Compare)
    current_dict = canonicalize(exams)
    previous = load_cache_from_gcs(CACHE_FILE_NAME)
    
    # Data loss check (warning only)
    if len(current_dict) < len(previous):
         logger.warning(f"[{fetch_source}] Fetched {len(current_dict)} records, cache has {len(previous)}.")
    
    changes = get_changes(current=current_dict, previous=previous)

    if changes:
        logger.info(f"{len(changes)} changes detected compared to cache.")
        save_cache_to_gcs(current_dict, CACHE_FILE_NAME)
        send_notification(changes)
        
        # Trigger MacroDroid webhook
        macrodroid_url = os.getenv("MACRODROID_WEBHOOK_URL")
        if macrodroid_url:
            try:
                import requests
                logger.info("Triggering MacroDroid webhook...")
                requests.get(macrodroid_url, timeout=30)
                logger.info("MacroDroid webhook triggered successfully.")
            except Exception as e:
                logger.error(f"Failed to trigger MacroDroid webhook: {e}")
    else:
        logger.info("No changes vs cache.")


def run() -> None:
    """Runs all available grade monitors."""
    # Run IMS
    try:
        logger.info("--- Running IMS Monitor ---")
        monitor_with_ims()
        logger.info("--- IMS Monitor Finished ---")
    except Exception as e:
        logger.error(f"IMS Monitor Failed: {e}")
        _send_telegram_message(f"IMS Monitor Failed: {e}")

    # Run Portal Monitor (API + Fallback)
    logger.info("--- Running Portal Monitor ---")
    try:
        monitor_grades_with_fallback()
        logger.info("--- Portal Monitor Finished Successfully ---")
    except Exception as e:
        logger.error(f"Portal Monitor Failed: {e}")
        _send_telegram_message(f"❌ Portal Monitor Failed: {e}")

def main(request):
    """Cloud Function entry point."""
    try:
        run()
        return "Script executed successfully.", 200
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return "An error occurred.", 500

if __name__ == "__main__":
    run()

