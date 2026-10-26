"""Tests for URL handling and the FB/d entry-id metadata extractor."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forms.parser import canonical_urls, form_id_from_url, is_google_forms_url  # noqa: E402


def test_detects_google_forms_urls():
    assert is_google_forms_url("https://docs.google.com/forms/d/e/1FAIpQLSd.../viewform")
    assert is_google_forms_url("https://docs.google.com/forms/d/1AbCdEfGhIjK/viewform")
    assert is_google_forms_url("https://forms.gle/AbCdEf123456")
    assert is_google_forms_url("https://docs.google.com/forms/u/1/d/e/XYZ/viewform?usp=pp_url")
    assert not is_google_forms_url("https://docs.google.com/document/d/1AbC/edit")
    assert not is_google_forms_url("https://example.com/forms/d/e/X/viewform")


def test_form_id_extraction():
    assert form_id_from_url("https://docs.google.com/forms/d/e/1FAIpQLSdAbCdEfGhIjKlMn/viewform") == (
        "1FAIpQLSdAbCdEfGhIjKlMn"
    )
    assert form_id_from_url("https://docs.google.com/forms/d/1AbCdEfGhIjKlMnOp/viewform") == (
        "1AbCdEfGhIjKlMnOp"
    )
    assert form_id_from_url("https://docs.google.com/forms/u/2/d/e/1FAIpQLScAbCdEfGhIjKlMn/viewform") == (
        "1FAIpQLScAbCdEfGhIjKlMn"
    )
    assert form_id_from_url("https://forms.gle/AbCdEf123456") is None


def test_canonical_urls_preserves_shape():
    view, submit = canonical_urls("https://docs.google.com/forms/d/e/1FAIpQLSdAbCdEf/viewform")
    assert view.endswith("/viewform") and submit.endswith("/formResponse")
    assert "/d/e/" in view

    view, submit = canonical_urls("https://docs.google.com/forms/d/1AbCdEfGhIjKlMn/viewform")
    assert "/d/e/" not in view and submit.endswith("/formResponse")

    view, submit = canonical_urls("https://docs.google.com/forms/u/1/d/e/1FAIpQLScAbCdEf/viewform")
    assert "/forms/u/1/d/e/" in view