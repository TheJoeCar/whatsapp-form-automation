"""WhatsApp Web monitoring via Playwright.

WhatsApp Web obfuscates class names and occasionally changes its DOM, so this
module prefers role/aria-based selectors and treats class-based selectors as
config-overridable fallbacks. If extraction breaks, run
`python -m src.cli debug-whatsapp` to see which selectors still match.

Login happens once, headful, via QR scan (`python -m src.cli login-whatsapp`);
the session persists in the configured profile directory.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from ..config import WhatsAppConfig

WEB_URL = "https://web.whatsapp.com/"
LOGIN_TIMEOUT_S = 300


@dataclass
class WhatsAppMessage:
    message_id: str
    text: str
    meta: str  # e.g. "[18:04, 26/10/2026] Sender: " when available

    @classmethod
    def make(cls, message_id: str, text: str, meta: str) -> "WhatsAppMessage":
        if not message_id:
            message_id = hashlib.sha1(f"{meta}|{text}".encode()).hexdigest()[:16]
        return cls(message_id=message_id, text=text, meta=meta)


class WhatsAppMonitor:
    def __init__(self, config: WhatsAppConfig, headless: bool | None = None):
        self.config = config
        self.headless = config.headless if headless is None else headless
        self._pw = None
        self._ctx = None
        self.page = None

    # ------------------------------------------------------------------ #
    # Lifecycle

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.config.session_dir),
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.goto(WEB_URL, wait_until="domcontentloaded")

    def stop(self) -> None:
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self.page = self._ctx = self._pw = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # ------------------------------------------------------------------ #
    # Readiness / login

    def is_ready(self) -> bool:
        """True when the chat list has rendered (i.e. logged in)."""
        try:
            return self.page.locator('div[role="listitem"]').count() > 0
        except Exception:
            return False

    def has_qr(self) -> bool:
        try:
            return (
                self.page.locator("canvas").count() > 0
                or self.page.get_by_text(re_qr_hints()).count() > 0
            )
        except Exception:
            return False

    def wait_ready(self, timeout_s: int = LOGIN_TIMEOUT_S, quiet: bool = False) -> bool:
        """Wait for login. If a QR code is up, tell the user to scan it."""
        deadline = time.time() + timeout_s
        qr_announced = False
        while time.time() < deadline:
            if self.is_ready():
                if not quiet:
                    print("[whatsapp] logged in, chat list ready")
                return True
            if not qr_announced and self.has_qr() and not quiet:
                print(f"[whatsapp] QR code detected - scan it in the opened browser "
                      f"(or run `python -m src.cli login-whatsapp` headful first). "
                      f"Waiting up to {timeout_s}s...")
                qr_announced = True
            time.sleep(2)
        return False

    # ------------------------------------------------------------------ #
    # Channel + messages

    def open_channel(self, name: str) -> bool:
        """Open the configured chat/channel by searching for its name."""
        page = self.page
        box = self._find_search_box()
        if box is None:
            print("[whatsapp] could not find the search box - run debug-whatsapp")
            return False
        box.click()
        box.fill("")
        page.keyboard.type(name)
        time.sleep(1.5)  # let search results render

        items = page.get_by_role("listitem").filter(has_text=name)
        if items.count() == 0:
            print(f"[whatsapp] no chat matches {name!r} in search results")
            return False
        items.first.click()
        time.sleep(1.5)  # let the message list render
        return True

    def read_messages(self, limit: int | None = None) -> list[WhatsAppMessage]:
        """Read the most recent visible messages in the open chat."""
        limit = limit or self.config.max_messages_per_poll
        page = self.page
        row_sel = self.config.selectors.get("message_row", 'div[data-id]')
        text_sel = self.config.selectors.get("message_text", 'span.selectable-text')

        rows = page.locator(row_sel)
        total = rows.count()
        messages: list[WhatsAppMessage] = []
        for i in range(max(0, total - limit), total):
            row = rows.nth(i)
            try:
                row_id = row.get_attribute("data-id") or ""
                meta = row.get_attribute("data-pre-plain-text") or ""
                text_el = row.locator(text_sel).first
                text = text_el.inner_text(timeout=1000).strip() if text_el.count() > 0 else ""
            except Exception:
                continue
            if text and _is_real_message(text, meta):
                messages.append(WhatsAppMessage.make(row_id, text, meta))
        return messages

    def poll_new_messages(self) -> list[WhatsAppMessage]:
        """Convenience: open channel + read. Dedup happens in the pipeline."""
        if not self.open_channel(self.config.channel):
            return []
        return self.read_messages()

    # ------------------------------------------------------------------ #
    # Internals

    def _find_search_box(self):
        page = self.page
        # Role/aria first - survives class-name obfuscation.
        for box in page.get_by_role("textbox").all():
            try:
                label = (box.get_attribute("aria-label") or "") + (box.get_attribute("title") or "")
                if "search" in label.lower():
                    return box
            except Exception:
                continue
        # Fallbacks that have historically matched the search field.
        for sel in ('div[contenteditable="true"][data-tab="3"]', 'div[role="textbox"][contenteditable="true"]'):
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc.first
        return None

    def diagnostics(self) -> str:
        """Selector health report for debug-whatsapp."""
        page = self.page
        rows = []

        def count(sel: str) -> str:
            try:
                return f"{sel}: {page.locator(sel).count()} matches"
            except Exception as exc:
                return f"{sel}: ERROR {exc}"

        rows.append("=== selector diagnostics ===")
        for sel in (
            'div[role="listitem"]',
            'div[data-id]',
            'span.selectable-text',
            'div[data-pre-plain-text]',
            'div[contenteditable="true"]',
        ):
            rows.append(count(sel))
        labels = []
        for item in page.get_by_role("listitem").all()[:5]:
            labels.append(item.get_attribute("aria-label") or "?")
        rows.append(f"first listitem aria-labels: {labels}")
        return "\n".join(rows)


def _is_real_message(text: str, meta: str) -> bool:
    """Filter out UI noise like the encryption notice."""
    noise = ("end-to-end encrypted", "click to load", "unread messages")
    return not any(n in text.lower() for n in noise)


def re_qr_hints():
    import re

    return re.compile("scan me|link a device|use whatsapp on your", re.IGNORECASE)


def login_interactive(session_dir) -> bool:
    """One-time headful login: scan the QR code in the opened browser window.

    The session persists in `session_dir`, so later runs (headless) reuse it.
    """
    from ..config import WhatsAppConfig

    config = WhatsAppConfig(session_dir=session_dir)
    monitor = WhatsAppMonitor(config, headless=False)
    monitor.start()
    try:
        ok = monitor.wait_ready(quiet=False)
        print("[whatsapp] login OK - session saved" if ok else "[whatsapp] login timed out")
        return ok
    finally:
        monitor.stop()