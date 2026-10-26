"""Orchestration: poll WhatsApp → filter Google Forms links → parse → fill → submit."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

from .config import Config
from .forms.filler import FillAborted, fill_form
from .forms.parser import (
    canonical_urls,
    fetch_form_html,
    is_google_forms_url,
    parse_form_html,
)
from .forms.submitter import submit_form
from .llm.client import LLMClient, extract_json
from .requirements import load_requirements, evaluate_form_prompt, fill_form_prompt
from .state import StateStore
from .whatsapp.monitor import WhatsAppMessage, WhatsAppMonitor

URL_RE = re.compile(r"https?://\S+|(?<![\w@.])www\.\S+", re.IGNORECASE)

log = logging.getLogger("pipeline")


@dataclass
class FormOutcome:
    url: str
    action: str  # submitted | dry_run | skipped | error
    detail: str


class Pipeline:
    def __init__(self, config: Config, dry_run: bool | None = None, headless: bool | None = None):
        self.config = config
        self.dry_run = config.pipeline.dry_run if dry_run is None else dry_run
        self.state = StateStore(config.pipeline.state_file)
        self.requirements_text = self._load_requirements(config)
        self.llm = self._build_llm()
        self._headless = headless

    def _load_requirements(self, config: Config) -> str | None:
        text = load_requirements(config.forms.requirements_file)
        if text is None:
            log.warning(
                "no requirements document at %s — every form will be filled (legacy test mode)",
                config.forms.requirements_file,
            )
        return text

    def _build_llm(self) -> LLMClient | None:
        cfg = self.config.llm
        api_key = cfg.resolve_api_key()
        if cfg.provider == "openrouter" and not api_key:
            log.warning("no %s set - LLM filling disabled, heuristics will be used", cfg.api_key_env)
            return None
        return LLMClient(
            base_url=cfg.base_url,
            model=cfg.model,
            api_key=api_key,
            temperature=cfg.temperature,
            max_retries=cfg.max_retries,
        )

    # ------------------------------------------------------------------ #
    # Main loops

    def run_once(self) -> int:
        """One poll cycle (launches its own browser). Returns messages handled."""
        headless = self._headless if self._headless is not None else self.config.whatsapp.headless
        with WhatsAppMonitor(self.config.whatsapp, headless=headless) as monitor:
            if not monitor.wait_ready(quiet=False):
                raise RuntimeError("WhatsApp Web did not become ready (not logged in?)")
            return self._poll_once(monitor)

    def run_forever(self) -> None:
        """Keep one browser session open and poll on an interval."""
        interval = self.config.whatsapp.poll_interval_seconds
        headless = self._headless if self._headless is not None else self.config.whatsapp.headless
        log.info("polling %r every %ss (Ctrl+C to stop)", self.config.whatsapp.channel, interval)
        while True:
            try:
                with WhatsAppMonitor(self.config.whatsapp, headless=headless) as monitor:
                    if not monitor.wait_ready(quiet=False):
                        raise RuntimeError("WhatsApp Web did not become ready (not logged in?)")
                    log.info("monitor ready - watching %r", self.config.whatsapp.channel)
                    while True:
                        handled = self._poll_once(monitor)
                        if handled:
                            log.info("handled %d new message(s)", handled)
                        time.sleep(interval)
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("poll cycle failed - reopening browser next interval")
                time.sleep(interval)

    def _poll_once(self, monitor: WhatsAppMonitor) -> int:
        """Poll using an already-open monitor. Returns messages handled."""
        if not monitor.open_channel(self.config.whatsapp.channel):
            log.warning("could not open channel %r this cycle", self.config.whatsapp.channel)
            return 0
        messages = monitor.read_messages()
        fresh = [m for m in messages if not self.state.seen(m.message_id)]
        log.info("read %d message(s), %d new", len(messages), len(fresh))
        for message in fresh:
            outcome = self._handle_message(message)
            self.state.mark(message.message_id, outcome)
        if fresh:
            self.state.save()
        return len(fresh)

    # ------------------------------------------------------------------ #
    # Steps

    def _handle_message(self, message: WhatsAppMessage) -> str:
        urls = [_ensure_scheme(u) for u in URL_RE.findall(message.text)]
        urls += [u for u in message.links if u.startswith("http")]
        # De-duplicate while preserving order.
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]

        log.info("message %s: %d URL(s) found", message.message_id, len(urls))
        form_urls = [u for u in urls if is_google_forms_url(u)]

        if not form_urls:
            return "no_form_link" if urls else "no_link"

        results = []
        for url in form_urls:
            outcome = self._process_form(url)
            results.append(outcome)
        return "; ".join(f"{o.action}: {o.detail}" for o in results) or "handled"

    def _process_form(self, url: str) -> FormOutcome:
        try:
            html, viewform = fetch_form_html(url)
            form = parse_form_html(html, url=viewform)
            if not form.fields:
                return FormOutcome(url, "skipped", "no fields parsed - run parse-form to calibrate")

            log.info("parsed form %r with %d field(s)", form.title, len(form.fields))

            # Requirements gate: ask the LLM if the form qualifies.
            if self.requirements_text and self.llm is not None:
                try:
                    field_summaries = [
                        {
                            "entry_id": f.entry_id,
                            "type": f.type,
                            "required": f.required,
                            "title": f.title,
                            "options": f.options,
                        }
                        for f in form.fields
                    ]
                    gate_prompt = evaluate_form_prompt(
                        self.requirements_text,
                        form.title or "(untitled)",
                        field_summaries,
                    )
                    gate_reply = self.llm.complete(
                        "Read the requirements document and decide if this form qualifies. "
                        "Reply with ONLY a JSON object: {\"qualifies\": true/false, \"reason\": \"...\"}",
                        gate_prompt,
                        max_tokens=300,
                    )
                    gate = extract_json(gate_reply)
                    if not gate.get("qualifies"):
                        reason = gate.get("reason", "does not match requirements")
                        log.info("skipping form %r: %s", form.title or url, reason)
                        return FormOutcome(url, "skipped", reason)
                except Exception as exc:  # noqa: BLE001 - gate failure doesn't kill the pipeline
                    log.warning("requirements gate failed, proceeding anyway: %s", exc)

            # Fill the form using the LLM + requirements doc.
            values, warnings = fill_form(
                form,
                llm=self.llm,
                requirements_text=self.requirements_text,
                use_llm=self.llm is not None,
            )
            for warning in warnings:
                log.warning("filler: %s", warning)

            if self.dry_run:
                log.info("DRY RUN - would POST %s with: %s", form.submit_url, values)
                return FormOutcome(url, "dry_run", f"{len(values)} field(s) filled (dry run)")

            result = submit_form(form, values)
            level = log.info if result.ok else log.error
            level("submit %s -> %s", form.submit_url, result.detail)
            return FormOutcome(url, "submitted" if result.ok else "error", result.detail)
        except FillAborted as exc:
            log.info("not filling form %s: %s", url, exc)
            return FormOutcome(url, "skipped", str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the pipeline alive per-form
            log.exception("form processing failed for %s", url)
            return FormOutcome(url, "error", str(exc))


def _ensure_scheme(url: str) -> str:
    if url.startswith("www."):
        return "https://" + url
    return url