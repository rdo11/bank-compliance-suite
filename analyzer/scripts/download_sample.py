"""
download_sample.py — fetch a real Danish annual report for testing.

Usage:
    python scripts/download_sample.py                     # Novo Nordisk 2024
    python scripts/download_sample.py --url <url> --out sample_data/report.pdf

Default URL: Novo Nordisk's 2024 Annual Report (public PDF, hosted by the
company's IR pages). If the URL 404s, check the company's Investor
Relations site for a newer "Annual Report" PDF link and pass --url.

NOTE: Novo Nordisk publishes annual reports in English; for Danish
company filings (årsrapport) you can also use the Danish Business
Authority (virk.dk / datacvr.virk.dk) or the company's own IR pages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

DEFAULT_URL = (
    "https://www.novonordisk.com/content/dam/nncorp/global/en/investors/"
    "irmaterial/annual_report/2025/novo-nordisk-annual-report-2024.pdf"
)

KNOWN_ALTERNATIVES = [
    # archive of the 2023 report
    "https://www.novonordisk.com/content/dam/nncorp/global/en/investors/"
    "irmaterial/annual_report/2024/novo-nordisk-annual-report-2023.pdf",
    # Novo Holdings (Danish group) annual report 2024
    "https://assets.novoholdings.dk/novo-holdings-2024-annual-report.pdf",
]


def download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    resp = requests.get(url, timeout=120, allow_redirects=True)
    resp.raise_for_status()
    if "pdf" not in (resp.headers.get("content-type", "") or ""):
        print("WARNING: server did not return Content-Type: application/pdf "
              f"(got {resp.headers.get('content-type')})")
    out.write_bytes(resp.content)
    print(f"Saved {len(resp.content) / 1e6:.1f} MB -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--out", default="sample_data/sample_annual_report.pdf")
    args = ap.parse_args()

    out = Path(args.out)
    try:
        download(args.url, out)
        return 0
    except requests.HTTPError as exc:
        print(f"Failed ({exc}). Trying known alternatives...")
        for alt in KNOWN_ALTERNATIVES:
            try:
                download(alt, out)
                return 0
            except requests.HTTPError:
                continue
        print("All known URLs failed. Find the current report URL and pass --url.")
        return 1


if __name__ == "__main__":
    sys.exit(main())