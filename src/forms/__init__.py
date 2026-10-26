"""Google Forms parsing, filling and submission."""

from .parser import FormField, GoogleForm, canonical_urls, fetch_form_html, is_google_forms_url, parse_form_html, resolve_url
from .filler import fill_form
from .submitter import submit_form

__all__ = [
    "FormField",
    "GoogleForm",
    "canonical_urls",
    "fetch_form_html",
    "is_google_forms_url",
    "resolve_url",
    "parse_form_html",
    "fill_form",
    "submit_form",
]