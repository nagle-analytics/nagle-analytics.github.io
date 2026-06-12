#!/usr/bin/env python3
"""
Discover candidate data endpoints used by the USL Championship standings page.

This script opens the official standings page in a real browser, records network
requests and response previews, and saves candidate URLs/responses that may
contain the standings data.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


SOURCE_URL = "https://www.uslchampionship.com/league-standings"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "usl"
OUT_PATH = DATA_DIR / "debug-discovered-endpoints.txt"

KEYWORDS = [
    "standings",
    "standing",
    "Tampa Bay Rowdies",
    "Louisville City FC",
    "San Antonio FC",
    "Eastern Conference",
    "Western Conference",
    "competition",
    "table",
    "rank",
    "points",
    "team",
    "club",
    "opta",
    "widgets",
    "view",
    "match",
    "season",
]


def is_candidate_url(url: str) -> bool:
    text = url.lower()

    keep_terms = [
        "opta",
        "widget",
        "widgets",
        "view?",
        "stand",
        "competition",
        "team",
        "club",
        "season",
        "uslchampionship",
        "assets.ngin.com",
        "championship_league_script",
    ]

    drop_terms = [
        "google-analytics",
        "googletagmanager",
        "doubleclick",
        "facebook",
        "youtube",
        "recaptcha",
        "osano",
        "pubads",
        "gstatic",
        "fonts",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".woff",
        ".woff2",
        ".css",
    ]

    if any(term in text for term in drop_terms):
        return False

    return any(term in text for term in keep_terms)


def safe_preview(text: str, limit: int = 3000) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    return text[:limit]


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    records = []
    seen_urls = set()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1600, "height": 1200},
        )

        def handle_response(response):
            url = response.url

            if url in seen_urls:
                return

            if not is_candidate_url(url):
                return

            seen_urls.add(url)

            try:
                status = response.status
                headers = response.headers
                content_type = headers.get("content-type", "")
                body = ""

                try:
                    body = response.text()
                except Exception as exc:
                    body = f"[Could not read response text: {exc}]"

                body_lower = body.lower()

                keyword_hits = [
                    keyword for keyword in KEYWORDS
                    if keyword.lower() in body_lower or keyword.lower() in url.lower()
                ]

                records.append(
                    {
                        "url": url,
                        "status": status,
                        "content_type": content_type,
                        "keyword_hits": keyword_hits,
                        "length": len(body),
                        "preview": safe_preview(body),
                    }
                )

            except Exception as exc:
                records.append(
                    {
                        "url": url,
                        "status": "ERROR",
                        "content_type": "",
                        "keyword_hits": [],
                        "length": 0,
                        "preview": str(exc),
                    }
                )

        page.on("response", handle_response)

        page.goto(SOURCE_URL, wait_until="networkidle", timeout=90000)

        # Scroll slowly so lazy-loaded standings/widgets have a chance to load.
        for y in [400, 800, 1200, 1600, 2200]:
            page.mouse.wheel(0, y)
            page.wait_for_timeout(1500)

        rendered_text = page.locator("body").inner_text(timeout=10000)
        rendered_html = page.content()

        records.append(
            {
                "url": "[RENDERED_PAGE_BODY_TEXT]",
                "status": "OK",
                "content_type": "text/plain",
                "keyword_hits": [
                    keyword for keyword in KEYWORDS
                    if keyword.lower() in rendered_text.lower()
                ],
                "length": len(rendered_text),
                "preview": safe_preview(rendered_text, limit=5000),
            }
        )

        records.append(
            {
                "url": "[RENDERED_PAGE_HTML]",
                "status": "OK",
                "content_type": "text/html",
                "keyword_hits": [
                    keyword for keyword in KEYWORDS
                    if keyword.lower() in rendered_html.lower()
                ],
                "length": len(rendered_html),
                "preview": safe_preview(rendered_html, limit=5000),
            }
        )

        browser.close()

    lines = [
        f"Source page: {SOURCE_URL}",
        f"Candidate responses captured: {len(records)}",
        "",
    ]

    for i, record in enumerate(records, start=1):
        lines.append("=" * 100)
        lines.append(f"Candidate {i}")
        lines.append(f"URL: {record['url']}")
        lines.append(f"Status: {record['status']}")
        lines.append(f"Content-Type: {record['content_type']}")
        lines.append(f"Length: {record['length']}")
        lines.append(f"Keyword hits: {', '.join(record['keyword_hits'])}")
        lines.append("")
        lines.append("Preview:")
        lines.append(record["preview"])
        lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"[INFO] Saved discovered endpoint debug file to: {OUT_PATH}")
    print(f"[INFO] Captured {len(records)} candidate response(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
