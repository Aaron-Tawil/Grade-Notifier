#!/usr/bin/env python3
"""Ad-hoc harness to exercise the IMS flow with TLS verification enabled."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ims import GradeInfo, IMS


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"Missing required environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return value


def _default_years() -> List[int]:
    current_year = datetime.now().year
    return [current_year - 1, current_year, current_year + 1]


def _format_grade(grade: GradeInfo) -> str:
    grade_display = "Exempt" if grade.is_exempt else (str(grade.grade) if grade.grade is not None else "N/A")
    return f"{grade.course_id} ({grade.semester}) -> {grade_display}"


def main(argv: Iterable[str] | None = None) -> None:
    load_dotenv(dotenv_path=ROOT / ".env")

    parser = argparse.ArgumentParser(
        description=(
            "Runs the IMS fetcher with SSL verification enabled to ensure the upstream "
            "flow still works. Provide UNI_USER, UNI_PASS, and UNI_ID via the environment."
        )
    )
    parser.add_argument(
        "--years",
        nargs="*",
        type=int,
        help="Specific academic years to request (defaults to current year ±1).",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Allow disabling SSL verification for comparison/debugging",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=5,
        help="How many grade entries to print for inspection (default: 5)",
    )
    parser.add_argument(
        "--ca-bundle",
        type=Path,
        help="Optional path to a PEM file containing the certificate chain to trust.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    username = _require_env("UNI_USER")
    password = _require_env("UNI_PASS")
    student_id = _require_env("UNI_ID")

    years = args.years if args.years else _default_years()
    verify_ssl = not args.no_verify
    if args.ca_bundle:
        verify_ssl = str(args.ca_bundle)

    print(
        f"Starting IMS fetch with SSL verification {'ENABLED' if verify_ssl else 'DISABLED'} "
        f"for years: {years}"
    )

    ims = IMS(username=username, id=student_id, password=password, verify_ssl=verify_ssl)
    grades = ims.get_all_grades(years=years)

    print(f"Done. Retrieved {len(grades)} grade records from IMS.")
    if not grades:
        return

    grades.sort(key=lambda g: (g.semester, g.course_id))
    sample_size = min(args.sample, len(grades))
    print(f"Showing {sample_size} sample entries:")
    for grade in grades[:sample_size]:
        print("  -", _format_grade(grade))


if __name__ == "__main__":
    main()
