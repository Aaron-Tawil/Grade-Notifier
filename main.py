from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Optional

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

from dataclasses import asdict
from ims import IMS, GradeInfo

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
            notebook_available = notebook_button.get_attribute("disabled") is None
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

    print("No grade table detected; returning empty result.")
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
            "notebook_available": "true" if rec.get("notebook_available") else "false",
            "raw_text": rec.get("raw_text", ""),
        }
    return result


def print_preview(current: Dict[str, Dict[str, str]]) -> None:
    print("\n=== Parsed grades preview ===")
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
        print(f"{title}  |  Grade: {grade}{suffix}")
    print("=== end ===\n")


def save_debug_to_gcs(page, tag: str = "debug") -> None:
    html_path = OUT_DIR / f"{tag}.html"
    png_path = OUT_DIR / f"{tag}.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(png_path), full_page=True)
    except Exception as exc:
        print(f"Error writing local debug artifacts: {exc}")

    if not GCS_BUCKET_NAME or storage is None:
        return
    try:
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        bucket.blob(f"{ARTIFACT_PREFIX}/{tag}.html").upload_from_filename(str(html_path))
        bucket.blob(f"{ARTIFACT_PREFIX}/{tag}.png").upload_from_filename(str(png_path))
    except Exception as exc:  # pragma: no cover - network access
        print(f"Error saving debug artifacts to GCS: {exc}")


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
        print(f"Error loading cache from GCS ({cache_file}): {exc}")
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
        print(f"Error saving cache to GCS ({cache_file}): {exc}")


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
        print("Telegram credentials not found. Skipping notification.")
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
            print("--- Telegram Notification Sent Successfully! ---")
        else:
            print("--- Failed to Send Telegram Notification ---")
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"--- An error occurred while sending Telegram notification: {e} ---")


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
        print("Warning: 'data/courses.json' not found. Course names will be unknown.")
        return {}
    except json.JSONDecodeError:
        print("Warning: Failed to decode JSON from 'data/courses.json'. Course names will be unknown.")
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
    print("[login] logging in")
    page.wait_for_load_state("domcontentloaded", timeout=max_wait_ms)
    page.wait_for_function(
        """
        () => !!document.querySelector('input[type="password"]') ||
              !!document.querySelector('button, input[type="submit"]')
        """,
        timeout=max_wait_ms,
    )
    print("searching usename input")

    user_loc = None
    user_selector_used = None
    for selector in (
        "input[name='user_name']",
        "input[autocomplete='username']",
        "input[type='email']",
        "input[type='text']",
    ):
        candidate = page.locator(selector)
        print(candidate)
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
    print(f"[login] user selector: {user_selector_used}, id selector: {id_selector_used}, pass available: {bool(pass_loc)}")

    if user_loc:
        try:
            user_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        print("[login] user locator found")
        user_loc.fill(user)
    else:
        print("[login] user locator not found")

    if national_id and id_loc:
        try:
            id_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        id_loc.fill(national_id)
    elif national_id:
        print("[login] ID locator not found")

    if pass_loc:
        try:
            pass_loc.wait_for(state="visible", timeout=max_wait_ms)
        except Exception:
            pass
        pass_loc.fill(pwd)
    else:
        print("[login] password locator not found")

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
        page.wait_for_timeout(2000)  # Wait a bit for filters to be interactive

        # The correct selector for the 'x' button, found via inspection.
        # The element is a <span> with role="button".
        remove_button_selector = ".vscomp-value-tag-clear-button"
        
        remove_buttons = page.locator(remove_button_selector)
        count = remove_buttons.count()

        if count > 0:
            print(f"Found {count} filter remove buttons to click.")
            # Iterate backwards to safely handle DOM changes while clicking.
            for i in range(count - 1, -1, -1):
                try:
                    remove_buttons.nth(i).click(timeout=5000)
                    page.wait_for_timeout(500)  # Brief pause after click.
                except Exception as e:
                    print(f"Could not click filter remove button at index {i}: {e}")
        else:
            print("No filter remove buttons found. Filters may already be clear.")

        # After clearing filters, the table should refresh.
        # We wait for the network to be idle to ensure the data is updated.
        page.wait_for_load_state("networkidle", timeout=45000)
        print("Network is idle after attempting to clear filters.")

    except PWTimeout:
        print("Timeout waiting for network idle after attempting to clear filters.")
        pass
    except Exception as exc:
        print(f"An error occurred during filter clearing: {exc}")


def monitor_with_playwright() -> None:
    if not UNI_USER or not UNI_PASS:
        print("Set UNI_USER and UNI_PASS environment variables before running.")
        sys.exit(1)

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            context_kwargs = {
                "viewport": DESKTOP_VIEWPORT,
                "user_agent": DESKTOP_USER_AGENT,
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
            page.goto(GRADES_URL, wait_until="networkidle", timeout=60000)
            bypass_intro(page)
            page.wait_for_load_state("networkidle", timeout=60000)
            bypass_intro(page)
            


            login_selectors = {
                "user": "input[name='user_name']",
                "id": "input[name='id_number']",
                "password": "input[name='password']",
            }
            selector_counts = {key: page.locator(sel).count() for key, sel in login_selectors.items()}
            need_login = any(count > 0 for count in selector_counts.values())
            url_login = "nidp" in page.url or "edp_login" in page.url
            print(f"[login] url={page.url} need_login={need_login} selector_counts={selector_counts} url_login={url_login}")

            if need_login or url_login or True:
                print("[login] entering login flow")
                taunidp_login(page, UNI_USER, UNI_PASS, UNI_ID)
                try:
                    page.wait_for_load_state("networkidle", timeout=60000)
                except PWTimeout:
                    pass
                page.goto(GRADES_URL, wait_until="networkidle", timeout=60000)
            else:
                print("[login] already authenticated")
                page.goto(GRADES_URL, wait_until="networkidle", timeout=60000)

            # bypass_intro(page)

            apply_default_filters(page)
            try:
                page.wait_for_selector(TABLE_SELECTOR, timeout=45000)
            except PWTimeout:
                print("Grade table selector not found within timeout.")
            exams = extract_exam_details(page)
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
            changes = get_changes(current=current_dict, previous=previous)

            if changes:
                print(f"{len(changes)} changes detected compared to cache.")
                save_cache_to_gcs(current_dict, CACHE_FILE_NAME)
                send_notification(changes)

                # Trigger MacroDroid webhook
                try:
                    import requests
                    url = "https://trigger.macrodroid.com/61631bb4-126f-4ccc-8dc1-be952baf6193/grade"
                    print(f"Triggering MacroDroid webhook: {url}")
                    requests.get(url, timeout=5)
                    print("MacroDroid webhook triggered successfully.")
                except Exception as e:
                    print(f"Failed to trigger MacroDroid webhook: {e}")
            else:
                print("No changes vs cache.")
        finally:
            if context:
                context.close()
            if browser:
                browser.close()


def monitor_with_ims() -> None:
    """Fetches grades using the IMS API and notifies of changes."""
    if not all([UNI_USER, UNI_ID, UNI_PASS]):
        print("IMS credentials (UNI_USER, UNI_ID, UNI_PASS) not set. Skipping.")
        return

    print("Fetching grades via IMS API...")
    ims = IMS(username=UNI_USER, id=UNI_ID, password=UNI_PASS)
    
    # Fetch for current and surrounding years for safety
    current_year = datetime.now().year
    years_to_fetch = [current_year - 1, current_year, current_year + 1, current_year + 2]
    
    current_grades = ims.get_all_grades(years=years_to_fetch)
    
    # Sort for consistency
    current_grades.sort(key=lambda g: (g.semester, g.course_id))
    
    print(f"Found {len(current_grades)} grades via IMS.")
    if not current_grades:
        print("No grades found via IMS. Skipping further processing.")
        return

    # Load previous state from cache
    previous_grades_raw = load_cache_from_gcs(IMS_CACHE_FILE_NAME)
    previous_grades = [GradeInfo(**g) for g in previous_grades_raw] if previous_grades_raw else []

    # Detect changes
    changes = get_ims_changes(current_grades, previous_grades)

    if changes:
        print(f"{len(changes)} changes detected in IMS grades.")
        
        # Fetch course names for user-friendly notifications
        course_names = fetch_course_names()
        
        # Send notification
        send_ims_notification(changes, course_names)
        
        # Save new state to cache
        # Convert list of dataclasses to list of dicts for JSON serialization
        save_cache_to_gcs([asdict(g) for g in current_grades], IMS_CACHE_FILE_NAME)

        # Trigger MacroDroid webhook
        try:
            import requests
            url = "https://trigger.macrodroid.com/61631bb4-126f-4ccc-8dc1-be952baf6193/grade"
            print(f"Triggering MacroDroid webhook: {url}")
            requests.get(url, timeout=5)
            print("MacroDroid webhook triggered successfully.")
        except Exception as e:
            print(f"Failed to trigger MacroDroid webhook: {e}")
    else:
        print("No changes in IMS grades vs cache.")


def run() -> None:
    """Runs all available grade monitors."""
    monitors = {
        "Playwright": monitor_with_playwright,
        "IMS": monitor_with_ims,
    }
    
    for name, monitor_func in monitors.items():
        try:
            print(f"--- Running {name} Monitor ---")
            monitor_func()
            print(f"--- {name} Monitor Finished ---")
        except Exception as e:
            print(f"--- {name} Monitor Failed ---")
            print(f"An error occurred in {name} monitor: {e}")
            _send_telegram_message(
                f"🔴 Grade Notifier CRITICAL 🔴\n\n"
                f"The *{name} monitor* failed with an error:\n\n"
                f"```\n{e}\n```",
                parse_mode="Markdown"
            )


def main(request):
    """Cloud Function entry point."""
    try:
        run()
        return "Script executed successfully.", 200
    except Exception as e:
        print(f"An error occurred: {e}")
        _send_telegram_message(
            f"🔴 Grade Notifier CRITICAL 🔴\n\n"
            f"The script failed with an unhandled error:\n\n"
            f"```\n{e}\n```",
            parse_mode="Markdown"
        )
        return "An error occurred.", 500

if __name__ == "__main__":
    run()

