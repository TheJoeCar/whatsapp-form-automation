"""Generate values for a parsed form: LLM first, deterministic fallback second.

Every value is validated against the parsed field types and option lists
before it is returned — an LLM suggestion that names an option that doesn't
exist gets dropped with a warning, never submitted.
"""

from __future__ import annotations

import re

from ..llm.client import LLMClient, extract_json
from .parser import FormField, GoogleForm

_CHOICE_TYPES = {"multiple_choice", "dropdown", "linear_scale"}


def fill_form(
    form: GoogleForm,
    llm: LLMClient | None = None,
    test_data: dict | None = None,
    use_llm: bool = True,
) -> tuple[dict[str, str | list[str]], list[str]]:
    """Return (values: entry_id -> str | list[str], warnings)."""
    test_data = test_data or {}
    warnings: list[str] = []
    values: dict[str, str | list[str]] = {}

    if llm is not None and use_llm:
        try:
            llm_values = _llm_values(form, llm, test_data)
            values, drop_warnings = _validate_values(form, llm_values)
            warnings.extend(drop_warnings)
        except Exception as exc:  # noqa: BLE001 - any LLM failure must not kill the pipeline
            warnings.append(f"LLM filling failed, using heuristics: {exc}")

    if not values:
        values = {f.entry_id: _heuristic_value(f, test_data) for f in form.fields if _heuristic_value(f, test_data)}
        warnings.append("values from deterministic heuristics")

    # Guarantee required fields have something.
    for field in form.fields:
        if field.required and field.entry_id not in values:
            fallback = _heuristic_value(field, test_data)
            if fallback:
                values[field.entry_id] = fallback
                warnings.append(f"required field {field.entry_id} filled from heuristics")

    return values, warnings


def _llm_values(form: GoogleForm, llm: LLMClient, test_data: dict) -> dict:
    system = (
        "You fill Google Forms with plausible TEST data for forms the user owns. "
        "Reply with ONLY a JSON object mapping entry ids to values. Rules:\n"
        "- multiple_choice, dropdown, linear_scale: exactly one of the given option strings\n"
        "- checkboxes: a JSON array of 1-3 of the given option strings\n"
        "- short_answer, paragraph: a short realistic string\n"
        "- Use the provided test persona for name/email/phone fields\n"
        "- Every required field MUST appear in the JSON"
    )
    persona = "\n".join(f"- {k}: {v}" for k, v in test_data.items())
    field_lines = []
    for f in form.fields:
        line = f"- {f.entry_id} | type={f.type} | required={f.required} | title={f.title!r}"
        if f.options:
            line += f" | options={f.options!r}"
        field_lines.append(line)
    user = (
        f"Form title: {form.title}\n"
        f"Description: {form.description}\n\n"
        f"Test persona:\n{persona}\n\n"
        f"Fields:\n" + "\n".join(field_lines)
    )
    reply = llm.complete(system, user)
    parsed = extract_json(reply)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got: {type(parsed).__name__}")
    return {str(k): v for k, v in parsed.items()}


def _validate_values(form: GoogleForm, values: dict) -> tuple[dict, list[str]]:
    """Keep only values that fit the parsed form; return (clean, warnings)."""
    known = {f.entry_id: f for f in form.fields}
    clean: dict[str, str | list[str]] = {}
    warnings: list[str] = []

    for entry_id, value in values.items():
        field = known.get(entry_id)
        if field is None:
            warnings.append(f"dropped {entry_id}: not a field in this form")
            continue
        if field.type in _CHOICE_TYPES:
            value = _match_option(field, value, warnings)
            if value is None:
                continue
        elif field.type == "checkboxes":
            if not isinstance(value, list) or not value:
                value = [value]
            matched = []
            for item in value:
                opt = _match_option(field, item, warnings, fail_quietly=True)
                if opt is not None:
                    matched.append(opt)
            if not matched:
                warnings.append(f"dropped {entry_id}: no valid checkbox options")
                continue
            value = matched[:5]
        elif isinstance(value, list):
            value = " ".join(str(v) for v in value)
        clean[entry_id] = value

    return clean, warnings


def _match_option(field: FormField, value, warnings: list[str], fail_quietly: bool = False):
    if not isinstance(value, str):
        value = str(value)
    for option in field.options:
        if option == value:
            return option
    lowered = value.strip().lower()
    for option in field.options:
        if option.lower() == lowered:
            return option
    if fail_quietly:
        return None
    warnings.append(f"dropped {field.entry_id}: {value!r} is not one of {field.options!r}")
    return None


def _heuristic_value(field: FormField, test_data: dict) -> str | list[str] | None:
    title = field.title.lower()
    # Types we couldn't map (date/time pickers etc.) degrade to free-text.
    ftype = "short_answer" if field.type.startswith("unknown_type_") else field.type

    if field.type in _CHOICE_TYPES and field.options:
        return field.options[0]
    if field.type == "checkboxes" and field.options:
        return [field.options[0]]

    if field.input_type == "email" or "email" in title:
        return test_data.get("email", "testy.mctestface@example.com")
    if field.input_type == "tel" or "phone" in title or "mobile" in title:
        return test_data.get("phone", "+15550000001")
    if field.input_type == "number":
        return "1"
    if field.input_type == "date" or "date" in title:
        return "2026-01-01"
    if field.input_type == "time":
        return "12:00"
    if field.input_type == "url":
        return "https://example.com"
    if "name" in title:
        return test_data.get("name", "Testy McTestface")
    if any(word in title for word in ("organization", "organisation", "company", "school", "team")):
        return test_data.get("organization", "Automated Testing Co")
    if ftype == "paragraph":
        return "Automated test response - please ignore."
    if ftype == "short_answer" and field.required:
        return "Automated test response"
    return None