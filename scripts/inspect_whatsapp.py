"""Inspect the live WhatsApp Web DOM to calibrate selectors.

Usage:
    .venv/bin/python -m scripts.inspect_whatsapp          # headful
    .venv/bin/python -m src.cli debug-whatsapp --headed   # diagnostics only

Waits for the app to finish loading (can take 10-60s), then dumps:
  - candidate selector counts
  - chat list container structure (tags, roles, aria-labels, data-* attrs)
  - full HTML saved to /tmp/opencode/whatsapp_dom.html
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from src.config import load_config

DUMP_PATH = Path("/tmp/opencode/whatsapp_dom.html")

CANDIDATES = [
    '[role="listitem"]',
    '[data-testid="cell-frame-container"]',
    '[aria-label="Chat list"]',
    '#pane-side',
    '[role="grid"]',
    '[role="row"]',
    'div[data-id]',
    'span.selectable-text',
    'div[contenteditable="true"]',
    '[role="textbox"]',
    '[data-tab]',
    '[data-ref]',
    'canvas',
]


def dump_structure(el, depth=0, max_depth=7, out=None):
    """Print tag/role/aria-label/data-* for a subtree (skeleton only)."""
    out = out if out is not None else []
    if depth > max_depth:
        return out
    try:
        info = el.evaluate(
            """el => ({
                tag: el.tagName.toLowerCase(),
                role: el.getAttribute('role'),
                aria: el.getAttribute('aria-label'),
                testid: el.getAttribute('data-testid'),
                tab: el.getAttribute('data-tab'),
                id: el.id || null,
                cls: (el.className && typeof el.className === 'string') ? el.className.slice(0, 60) : null,
            })"""
        )
    except Exception:
        return out
    bits = [info["tag"]]
    for key, label in (("role", "role"), ("aria", "aria"), ("testid", "testid"), ("tab", "tab"), ("id", "id")):
        if info.get(key):
            bits.append(f'{label}="{info[key]}"')
    out.append("  " * depth + " ".join(bits))
    try:
        for child in el.query_selector_all(":scope > *"):
            dump_structure(child, depth + 1, max_depth, out)
    except Exception:
        pass
    return out


def main() -> None:
    config = load_config()
    session = Path(config.whatsapp.session_dir)
    print(f"session dir: {session}")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(session),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded")
        print("page loaded (domcontentloaded), waiting for app to render...")

        deadline = time.time() + 90
        last_report = 0
        ready = False
        while time.time() < deadline:
            counts = {}
            for sel in CANDIDATES:
                try:
                    n = page.locator(sel).count()
                except Exception:
                    n = -1
                counts[sel] = n
            if counts['#pane-side'] > 0 and counts['[role="listitem"]'] > 0:
                ready = True
                break
            if time.time() - last_report > 10:
                print(f"  waiting... {int(deadline - time.time())}s left | pane-side={counts['#pane-side']} "
                      f"listitem={counts['[role=\"listitem\"]']} data-id={counts['div[data-id]']} "
                      f"canvas={counts['canvas']}")
                last_report = time.time()
            time.sleep(2)

        print(f"\nready: {ready}  url: {page.url}  title: {page.title()!r}")
        print("\n=== selector counts ===")
        for sel in CANDIDATES:
            try:
                print(f"  {sel}: {page.locator(sel).count()}")
            except Exception as exc:
                print(f"  {sel}: ERROR {exc}")

        pane = page.locator('#pane-side')
        if pane.count() > 0:
            print("\n=== #pane-side skeleton (depth 6) ===")
            for line in dump_structure(pane.first, max_depth=6):
                print(line)
            # first few listitems with text
            print("\n=== first 10 listitem aria-labels ===")
            for i, item in enumerate(page.locator('#pane-side [role="listitem"]').all()[:10]):
                aria = item.get_attribute("aria-label") or "?"
                text = (item.inner_text() or "").strip().replace("\n", " | ")[:80]
                print(f"  {i + 1}. aria={aria!r} text={text!r}")
        else:
            print("\n!! #pane-side not found - dumping full body skeleton")
            for line in dump_structure(page.locator("body").first, max_depth=5):
                print(line)

        DUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        DUMP_PATH.write_text(page.content(), encoding="utf-8")
        print(f"\nfull HTML saved: {DUMP_PATH} ({DUMP_PATH.stat().st_size} bytes)")

        try:
            input("Press Enter to close the browser...")
        except EOFError:
            pass
        ctx.close()


if __name__ == "__main__":
    main()