#!/usr/bin/env python3
"""Small IMS diagnostics script for Windows/local debugging."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ims import IMS


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    load_dotenv(dotenv_path=ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Probe IMS pages and save HTML snapshots for troubleshooting."
    )
    parser.add_argument("--no-verify", action="store_true", help="Disable TLS verification.")
    parser.add_argument("--ca-bundle", type=Path, help="Optional PEM bundle path.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Default: tests/output/ims_probe_<timestamp>",
    )
    args = parser.parse_args()

    username = _require_env("UNI_USER")
    password = _require_env("UNI_PASS")
    student_id = _require_env("UNI_ID")

    verify_ssl: bool | str = not args.no_verify
    if args.ca_bundle:
        verify_ssl = str(args.ca_bundle)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir or (ROOT / "tests" / "output" / f"ims_probe_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output directory: {output_dir}")
    print(f"TLS verify: {verify_ssl}")

    ims = IMS(username=username, id=student_id, password=password, verify_ssl=verify_ssl)
    print("IMS sign-in completed.")

    page = ims.request_page("get", "TP/Tziunim_P.aspx", params={"src": "", "sys": "tal", "rightmj": 1})
    html = str(page)
    title = page.find("title")
    form = page.find("form", {"name": "frmfree"})

    print(f"Page title: {title.text.strip() if title and title.text else '<none>'}")
    print(f"Has frmfree form: {bool(form)}")
    print(f"Response text length: {len(html)}")

    html_path = output_dir / "tziunim_p.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"Saved: {html_path}")

    if form:
        tckeys = [i.get("value", "") for i in form.find_all("input", {"name": "tckey"})]
        print(f"tckey count: {len(tckeys)}")
    else:
        print("frmfree form missing. This explains the 'NoneType has no attribute find_all' error.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
