#!/usr/bin/env python3
"""Run API and DOM portal fetchers and compare their JSON outputs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from difflib import unified_diff
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(dotenv_path=ROOT / ".env")

from grade_fetcher import GradeFetcher
from main import canonicalize
from robust_scraper import RobustGradesScraper


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(_json_text(payload), encoding="utf-8")


def _date_only(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.split(" ", 1)[0].split("T", 1)[0]


def _to_simple_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    simplified = []
    for row in rows:
        simplified.append(
            {
                "course": str(row.get("course") or "").strip(),
                "date": _date_only(row.get("date")),
                "grade": str(row.get("grade") or "").strip(),
                "moed": str(row.get("moed") or "").strip(),
                "notebook_available": bool(row.get("notebook_available")),
            }
        )
    simplified.sort(key=lambda r: (r["course"], r["moed"], r["date"], r["grade"]))
    return simplified


def _ensure_credentials() -> tuple[str, str, str]:
    user = os.getenv("UNI_USER", "").strip()
    password = os.getenv("UNI_PASS", "").strip()
    user_id = os.getenv("UNI_ID", "").strip()
    if not all([user, password, user_id]):
        raise RuntimeError("Missing credentials. Set UNI_USER, UNI_PASS, and UNI_ID in environment/.env.")
    return user, password, user_id


def _run_api_fetch(headless: bool) -> tuple[list[dict[str, Any]], int]:
    fetcher = GradeFetcher(headless=headless)
    processed = fetcher.fetch_grades()
    captured = len(fetcher.fetched_data or [])
    return processed, captured


def _run_dom_fetch(headless: bool, user: str, password: str, user_id: str) -> list[dict[str, Any]]:
    with RobustGradesScraper(headless=headless) as scraper:
        if not scraper.login(user, password, user_id):
            raise RuntimeError("DOM login failed.")
        return scraper.scrape()


def _canonical_diff(left: dict[str, dict[str, str]], right: dict[str, dict[str, str]]) -> list[str]:
    left_text = _json_text(left).splitlines()
    right_text = _json_text(right).splitlines()
    return list(
        unified_diff(
            left_text,
            right_text,
            fromfile="api_canonical.json",
            tofile="dom_canonical.json",
            lineterm="",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Runs both portal grade processes (API interception + DOM fallback), saves JSON outputs, "
            "and checks whether canonicalized results are identical."
        )
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="Run browsers in headed mode (default is headless).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional output directory. Default: tests/output/compare_<timestamp>",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 when canonical JSON differs.",
    )
    args = parser.parse_args()

    user, password, user_id = _ensure_credentials()
    headless = not args.headful

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or (ROOT / "tests" / "output" / f"compare_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output directory: {output_dir}")
    print(f"Headless mode: {headless}")

    print("Running API fetcher...")
    api_raw, api_captured_count = _run_api_fetch(headless=headless)
    print(f"API captured records: {api_captured_count}")
    print(f"API records: {len(api_raw)}")

    print("Running DOM fetcher...")
    dom_raw = _run_dom_fetch(headless=headless, user=user, password=password, user_id=user_id)
    print(f"DOM records: {len(dom_raw)}")

    api_canonical = canonicalize(api_raw)
    dom_canonical = canonicalize(dom_raw)

    _write_json(output_dir / "api_raw.json", api_raw)
    _write_json(output_dir / "dom_raw.json", dom_raw)
    _write_json(output_dir / "api_simple_records.json", _to_simple_records(api_raw))
    _write_json(output_dir / "dom_simple_records.json", _to_simple_records(dom_raw))
    _write_json(output_dir / "api_canonical.json", api_canonical)
    _write_json(output_dir / "dom_canonical.json", dom_canonical)

    canonical_equal = api_canonical == dom_canonical
    print(f"Canonical identical: {canonical_equal}")

    report_lines: list[str] = []
    report_lines.append(f"api_raw_count={len(api_raw)}")
    report_lines.append(f"api_captured_count={api_captured_count}")
    report_lines.append(f"dom_raw_count={len(dom_raw)}")
    report_lines.append(f"api_canonical_count={len(api_canonical)}")
    report_lines.append(f"dom_canonical_count={len(dom_canonical)}")
    report_lines.append(f"canonical_identical={canonical_equal}")

    if not canonical_equal:
        api_keys = set(api_canonical.keys())
        dom_keys = set(dom_canonical.keys())
        only_api = sorted(api_keys - dom_keys)
        only_dom = sorted(dom_keys - api_keys)
        common = sorted(api_keys & dom_keys)
        changed = [key for key in common if api_canonical.get(key) != dom_canonical.get(key)]

        report_lines.append(f"only_api={len(only_api)}")
        report_lines.append(f"only_dom={len(only_dom)}")
        report_lines.append(f"changed_common={len(changed)}")

        if only_api:
            report_lines.append("sample_only_api=" + ", ".join(only_api[:10]))
        if only_dom:
            report_lines.append("sample_only_dom=" + ", ".join(only_dom[:10]))
        if changed:
            report_lines.append("sample_changed_common=" + ", ".join(changed[:10]))

        diff_lines = _canonical_diff(api_canonical, dom_canonical)
        diff_path = output_dir / "canonical.diff"
        diff_path.write_text("\n".join(diff_lines), encoding="utf-8")
        print(f"Diff written: {diff_path}")

    report_path = output_dir / "compare_report.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Report written: {report_path}")

    if args.strict and not canonical_equal:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
