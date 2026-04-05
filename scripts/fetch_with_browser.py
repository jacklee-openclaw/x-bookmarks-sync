#!/usr/bin/env python3
"""Browser-rendered page snapshot fetcher (for KB fallback).

Output JSON:
{
  "title": "...",
  "text": "...",
  "html": "...",
  "media": [{"url":"...", "alt":"...", "source":"browser-image"}],
  "source": "browser-playwright"
}
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
            html = page.content()
            html = (html or "")[:600000]
            media = page.evaluate(
                """
                () => {
                  const out = [];
                  const seen = new Set();
                  const nodes = Array.from(document.querySelectorAll('img'));
                  for (const img of nodes) {
                    const src = (img.currentSrc || img.src || '').trim();
                    if (!src || seen.has(src)) continue;
                    seen.add(src);
                    out.push({
                      url: src,
                      alt: (img.alt || '').trim(),
                      source: 'browser-image'
                    });
                    if (out.length >= 20) break;
                  }
                  return out;
                }
                """
            )
            print(
                json.dumps(
                    {
                        "title": title[:120],
                        "text": text,
                        "html": html,
                        "media": media,
                        "source": "browser-playwright",
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        finally:
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())
