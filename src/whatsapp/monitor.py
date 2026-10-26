"""WhatsApp Web monitoring via Playwright.

Verified against WhatsApp Web (Oct 2026 DOM):
  - chat list       : [data-testid="chat-list"] div[role="row"]
  - search box      : input[role="textbox"]  (aria-label is localized)
  - message rows    : [data-testid^="conv-msg-"] with data-id = message id
  - message text    : span.selectable-text inside the row
  - links           : <a href> inside the row (more reliable than text regex)
  - open chat panel : [data-testid="conversation-panel-messages"]

Announcements channels are detected by the header subtitle
("Nur Admins koennen Nachrichten senden" / "Only admins can send messages"),
which distinguishes the announcements channel from the community's general chat
when both rows share the community name.

Login happens once, headful, via QR scan (`python -m src.cli login-whatsapp`);
the session persists in the configured profile directory.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from ..config import WhatsAppConfig

WEB_URL = "https://web.whatsapp.com/"
LOGIN_TIMEOUT_S = 180

CHAT_LIST = '[data-testid="chat-list"]'
CHAT_ROWS = f'{CHAT_LIST} div[role="row"]'
SEARCH_BOX = 'input[role="textbox"]'
MESSAGE_ROWS = '[data-testid^="conv-msg-"]'
MESSAGE_TEXT = 'span.selectable-text'
CONVERSATION_PANEL = '[data-testid="conversation-panel-messages"]'
CHAT_HEADER = '[data-testid="conversation-info-header"]'

_NOISE = (
    "end-to-end encrypted",
    "ende-zu-ende-verschlüsselt",
    "click to load",
    "welcome to your community",
    "willkommen in deiner community",
    "unread messages",
)

_ADMIN_ONLY = ("admin", "administrator")  # matches EN + DE subtitle


@dataclass
class WhatsAppMessage:
    message_id: str
    text: str
    meta: str = ""  # e.g. "[18:04, 26/10/2026] Sender: " when available
    links: list[str] = field(default_factory=list)  # <a href> values in the row

    @classmethod
    def make(cls, message_id: str, text: str, meta: str = "", links: list[str] | None = None) -> "WhatsAppMessage":
        if not message_id:
            message_id = hashlib.sha1(f"{meta}|{text}|{'|'.join(links or [])}".encode()).hexdigest()[:16]
        return cls(message_id=message_id, text=text, meta=meta, links=links or [])


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
            return self.page.locator(CHAT_LIST).count() > 0
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
        """Wait for login/load, printing progress so it never looks hung."""
        deadline = time.time() + timeout_s
        last_report = 0.0
        qr_announced = False
        while time.time() < deadline:
            if self.is_ready():
                if not quiet:
                    print("[whatsapp] logged in, chat list ready")
                return True
            if not qr_announced and self.has_qr() and not quiet:
                print(f"[whatsapp] QR code detected - scan it in the opened browser "
                      f"(or run 'wa login-whatsapp' headful first). "
                      f"Waiting up to {timeout_s}s...")
                qr_announced = True
            now = time.time()
            if not quiet and now - last_report > 10:
                left = int(deadline - now)
                print(f"[whatsapp] waiting for chat list... {left}s left")
                last_report = now
            time.sleep(2)
        return False

    # ------------------------------------------------------------------ #
    # Channel + messages

    def open_channel(self, name: str) -> bool:
        """Open the configured chat/announcement channel by searching its name.

        When several rows share the name (community announcements vs general),
        prefer the one flagged admin-only in the chat header.
        """
        page = self.page
        box = page.locator(SEARCH_BOX).first
        if box.count() == 0:
            print("[whatsapp] search box not found - run debug-whatsapp")
            return False

        box.click()
        box.fill(name)
        time.sleep(1.5)  # search debounce

        rows = page.locator(CHAT_ROWS)
        matches = [
            i for i in range(rows.count())
            if name.lower() in (rows.nth(i).inner_text() or "").lower()
            and rows.nth(i).locator('[data-testid="cell-frame-container"]').count() > 0
        ]
        if not matches:
            preview = [((rows.nth(i).inner_text() or "").strip().replace("\n", " | "))[:60]
                       for i in range(min(5, rows.count()))]
            print(f"[whatsapp] no chat matches {name!r}; first rows: {preview}")
            return False

        first_with_name = False
        for i in matches[:3]:
            rows.nth(i).click()
            if not self._wait_conversation():
                continue
            header = self._header_text()
            first_with_name = first_with_name or name.lower() in header.lower()
            if any(tag in header.lower() for tag in _ADMIN_ONLY):
                return True
        return first_with_name

    def read_messages(self, limit: int | None = None) -> list[WhatsAppMessage]:
        """Read the most recent visible messages in the open chat."""
        limit = limit or self.config.max_messages_per_poll
        page = self.page
        rows = page.locator(MESSAGE_ROWS)
        total = rows.count()
        messages: list[WhatsAppMessage] = []
        for i in range(max(0, total - limit), total):
            row = rows.nth(i)
            try:
                message_id = row.get_attribute("data-id") or ""
                parts = row.locator(MESSAGE_TEXT).all_inner_texts()
                text = "\n".join(p.strip() for p in parts if p.strip())
                meta = row.evaluate(
                    """el => el.querySelector('[data-pre-plain-text]')?.getAttribute('data-pre-plain-text') || ''"""
                )
                links = row.evaluate(
                    """el => Array.from(el.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))"""
                ) or []
            except Exception:
                continue
            if not text and not links:
                continue  # system message (no copyable text)
            if _is_noise(text):
                continue
            messages.append(WhatsAppMessage.make(message_id, text, meta, links))
        return messages

    def poll_new_messages(self) -> list[WhatsAppMessage]:
        """Convenience: open channel + read. Dedup happens in the pipeline."""
        if not self.open_channel(self.config.channel):
            return []
        return self.read_messages()

    # ------------------------------------------------------------------ #
    # Internals

    def _wait_conversation(self, timeout_ms: int = 15000) -> bool:
        try:
            self.page.locator(CONVERSATION_PANEL).first.wait_for(state="attached", timeout=timeout_ms)
            time.sleep(1.0)  # let recent messages render
            return True
        except Exception:
            return False

    def _header_text(self) -> str:
        try:
            header = self.page.locator(CHAT_HEADER).first
            return (header.inner_text(timeout=2000) or "").strip()
        except Exception:
            return ""

    def diagnostics(self) -> str:
        """Selector health report for debug-whatsapp."""
        page = self.page
        rows = ["=== selector diagnostics ==="]
        for sel in (CHAT_LIST, CHAT_ROWS, SEARCH_BOX, MESSAGE_ROWS, MESSAGE_TEXT, CONVERSATION_PANEL, "canvas"):
            try:
                rows.append(f"{sel}: {page.locator(sel).count()} matches")
            except Exception as exc:
                rows.append(f"{sel}: ERROR {exc}")
        preview = []
        for i in range(min(8, self.page.locator(CHAT_ROWS).count())):
            text = (self.page.locator(CHAT_ROWS).nth(i).inner_text() or "").strip().replace("\n", " | ")
            preview.append(text[:80])
        rows.append(f"chat rows: {preview}")
        return "\n".join(rows)


def _is_noise(text: str) -> bool:
    lowered = text.lower()
    return any(noise in lowered for noise in _NOISE)


def re_qr_hints():
    return re.compile("scan me|link a device|use whatsapp on your|qr-code|qr code", re.IGNORECASE)


def login_interactive(session_dir) -> bool:
    """One-time headful login: scan the QR code in the opened browser window."""
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