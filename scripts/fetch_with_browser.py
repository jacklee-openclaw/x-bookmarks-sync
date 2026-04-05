#!/usr/bin/env python3
"""Browser-rendered page text fetcher (for KB fallback).

Output JSON: {"title": "...", "text": "...", "source": "browser-playwright"}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "missing url"}, ensure_ascii=False))
        return 2
    url = sys.argv[1]

    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print(json.dumps({"error": "playwright_not_installed"}, ensure_ascii=False))
        return 3

    user_data_dir = Path(
        os.environ.get("KB_BROWSER_USER_DATA_DIR", str(Path.home() / ".x_to_cdns-browser"))
    ).expanduser()
    headless = os.environ.get("KB_BROWSER_HEADLESS", "1") == "1"

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            title = (page.title() or "").strip()
            text = page.locator("body").inner_text(timeout=5000)
            text = " ".join(text.split())[:16000]
            print(json.dumps({"title": title[:120], "text": text, "source": "browser-playwright"}, ensure_ascii=False))
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
