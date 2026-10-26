"""Inspect an opened WhatsApp chat (message list DOM) for selector calibration.

Usage: .venv/bin/python -m scripts.inspect_chat "Test community"
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from src.config import load_config

DUMP_DIR = Path("/tmp/opencode")


def counts(page, selectors):
    out = []
    for sel in selectors:
        try:
            out.append(f"{sel}: {page.locator(sel).count()}")
        except Exception as exc:
            out.append(f"{sel}: ERROR {exc}")
    return out


MESSAGE_CANDIDATES = [
    '[data-testid="conversation-panel-messages"]',
    '[data-testid="msg-container"]',
    '[data-testid="msg-list-item"]',
    'div[data-pre-plain-text]',
    'span.selectable-text',
    'div.copyable-text',
    '[role="row"]',
    'div[data-id]',
]


def main(name: str) -> None:
    config = load_config()
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(config.whatsapp.session_dir),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded")

        print("waiting for chat list...")
        page.wait_for_selector('[data-testid="chat-list"]', timeout=90_000)
        time.sleep(2)

        # Search
        box = page.locator('input[role="textbox"]').first
        print(f"search box: aria={box.get_attribute('aria-label')!r}")
        box.click()
        box.fill(name)
        time.sleep(2)

        rows = page.locator('[data-testid="chat-list"] div[role="row"]')
        print(f"\n=== search results ({rows.count()} rows) ===")
        targets = []
        for i in range(rows.count()):
            row = rows.nth(i)
            text = (row.inner_text() or "").strip().replace("\n", " | ")
            print(f"  {i}: {text[:160]!r}")
            if name.lower() in row.inner_text().lower():
                rows_sel = f'[data-testid="chat-list"] div[role="row"] >> nth={i}'
                rows.nth(i).click()
                time.sleep(2.5)
                header = page.locator('[data-testid="conversation-info-header"]').first
                header_text = header.inner_text(timeout=2000).replace("\n", " | ") if header.count() else "(no header)"
                print(f"\n=== opened row {i} ===")
                print(f"  header: {header_text[:200]!r}")
                print("  message selector counts:")
                for line in counts(page, MESSAGE_CANDIDATES):
                    print(f"    {line}")

                # Dump last 5 message candidates with attributes
                for sel in ('[data-testid="msg-container"]', 'div[data-pre-plain-text]', 'div[data-id]'):
                    loc = page.locator(sel)
                    n = loc.count()
                    print(f"\n  last 5 of {sel} (n={n}):")
                    for i in range(max(0, n - 5), n):
                        el = loc.nth(i)
                        attrs = el.evaluate(
                            """el => ({testid: el.getAttribute('data-testid'), id: el.getAttribute('data-id'),
                                       pre: el.getAttribute('data-pre-plain-text'),
                                       text: (el.innerText||'').replace(/\\n/g,' | ').slice(0,120)})"""
                        )
                        print(f"    {i}: {attrs}")

        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        dump = DUMP_DIR / "whatsapp_chat_dom.html"
        DUMP_DIR.mkdir(parents=True, exist_ok=True)
        DUMP_DIR.joinpath("whatsapp_chat_dom.html").write_text(page.content(), encoding="utf-8")
        print(f"\nchat HTML saved: {dump} ({dump.stat().st_size} bytes)")

        try:
            input("Press Enter to close...")
        except EOFError:
            pass
        ctx.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "Test community")