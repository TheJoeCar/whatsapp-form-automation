"""Submit a parsed Google Form via the formResponse endpoint.

Note: single-page forms only (multi-page forms need one POST per page).
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .parser import USER_AGENT, GoogleForm

_SUCCESS_MARKERS = (
    "your response has been recorded",
    "response recorded",
    "formconfirmationsubmitted",
    "your response has been submitted",
)


@dataclass
class SubmitResult:
    ok: bool
    status_code: int | None
    detail: str


def build_payload(form: GoogleForm, values: dict[str, str | list[str]]) -> list[tuple[str, str]]:
    """Flatten values into (name, value) pairs; checkboxes repeat the entry name."""
    payload: list[tuple[str, str]] = []
    for entry_id, value in values.items():
        if isinstance(value, list):
            payload.extend((entry_id, str(item)) for item in value)
        else:
            payload.append((entry_id, str(value)))
    if form.fbzx:
        payload.append(("fbzx", form.fbzx))
    payload.append(("fvv", "1"))
    payload.append(("pageHistory", "0"))
    return payload


def submit_form(form: GoogleForm, values: dict[str, str | list[str]], timeout: float = 30.0) -> SubmitResult:
    resp = requests.post(
        form.submit_url,
        data=build_payload(form, values),
        headers={
            "User-Agent": USER_AGENT,
            "Referer": form.url,
            "Origin": "https://docs.google.com",
        },
        timeout=timeout,
    )

    if resp.status_code == 403:
        return SubmitResult(False, 403, "403 Forbidden - form may be closed, restricted, or CAPTCHA-protected")

    final_url = resp.url.lower()
    body = resp.text.lower()
    # Check for success markers first — the form page HTML always contains
    # fb_public_load_data_ even after a successful submission.
    if resp.status_code == 200 and any(marker in body or marker in final_url for marker in _SUCCESS_MARKERS):
        return SubmitResult(True, 200, "Response recorded")
    if resp.status_code == 200 and "fb_public_load_data_" in body:
        return SubmitResult(
            False, 200, "Form page re-served - submission was not accepted (check required fields / options)"
        )
    if resp.status_code == 200:
        return SubmitResult(False, 200, "200 but no confirmation marker - verify manually in the form responses tab")

    return SubmitResult(False, resp.status_code, f"HTTP {resp.status_code} from formResponse")