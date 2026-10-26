"""Generate values for a parsed form, driven by your identity + requirements.

Value priority per field: LLM suggestion (validated) -> identity/keyword
heuristics -> (only if rules allow) placeholder. Every value is validated
against the parsed field types and option lists before it is returned.
"""

from __future__ import annotations

import re

from ..llm.client import LLMClient, extract_json
from .parser import FormField, GoogleForm

_CHOICE_TYPES = {"multiple_choice", "dropdown", "linear_scale"}


class FillAborted(Exception):
    """Raised when rules forbid submitting (e.g. an unanswerable required field)."""


def fill_form(
    form: GoogleForm,
    llm: LLMClient | None = None,
    identity: dict | None = None,
    use_llm: bool = True,
    rules: dict | None = None,
) -> tuple[dict[str, str | list[str]], list[str]]:
    """Return (values: entry_id -> str | list[str], warnings).

    rules (optional, from requirements.yaml):
      fill_optional - also answer optional fields (default True, legacy mode)
      never_guess   - abort with FillAborted instead of placeholders (default False)
    """
    identity = identity or {}
    rules = rules or {}
    fill_optional = bool(rules.get("fill_optional", True))
    never_guess = bool(rules.get("never_guess", False))

    warnings: list[str] = []
    values: dict[str, str | list[str]] = {}

    targets = [f for f in form.fields if f.required or fill_optional]
    skipped = len(form.fields) - len(targets)
    if skipped:
        warnings.append(f"{skipped} optional field(s) skipped (fill_optional=false)")

    if llm is not None and use_llm:
        try:
            llm_values = _llm_values(form, llm, identity, targets, fill_optional, never_guess)
            llm_values, drop_warnings = _validate_values(form, llm_values)
            warnings.extend(drop_warnings)
            values.update(llm_values)
        except Exception as exc:  # noqa: BLE001 - any LLM failure must not kill the pipeline
            warnings.append(f"LLM filling failed, using identity heuristics: {exc}")

    if not values:
        values = {
            f.entry_id: v
            for f in targets
            if (v := _heuristic_value(f, identity, allow_placeholder=not never_guess)) is not None
        }
        warnings.append("values from identity heuristics")

    # Fill required fields the LLM missed.
    for field in targets:
        if field.required and field.entry_id not in values:
            fallback = _heuristic_value(field, identity, allow_placeholder=not never_guess)
            if fallback is not None:
                values[field.entry_id] = fallback
                warnings.append(f"required field {field.entry_id} filled from identity heuristics")

    if never_guess:
        missing = [
            f"{f.entry_id} ({f.title or 'untitled'})"
            for f in targets
            if f.required and f.entry_id not in values
        ]
        if missing:
            raise FillAborted("no confident answer for required field(s): " + ", ".join(missing))

    return values, warnings


def _llm_values(
    form: GoogleForm,
    llm: LLMClient,
    identity: dict,
    targets: list[FormField],
    fill_optional: bool,
    never_guess: bool,
) -> dict:
    system = (
        "You fill Google Forms on behalf of the user, using ONLY the identity data "
        "provided (plus the form's own options). Reply with ONLY a JSON object mapping "
        "entry ids to values. Rules:\n"
        "- multiple_choice, dropdown, linear_scale: exactly one of the given option strings\n"
        "- checkboxes: a JSON array of 1-5 of the given option strings\n"
        "- short_answer, paragraph: copy the matching identity value; if nothing matches, "
        "return null for that entry - NEVER invent facts\n"
        + ("- Answer required fields only.\n" if not fill_optional else "- Answer required and optional fields.\n")
        + ("- If a required field has no matching identity value, return null; the caller will abort.\n" if never_guess else "")
    )
    persona = _identity_prompt(identity)
    field_lines = []
    for f in targets:
        line = f"- {f.entry_id} | type={f.type} | required={f.required} | title={f.title!r}"
        if f.options:
            line += f" | options={f.options!r}"
        field_lines.append(line)
    user = (
        f"Form title: {form.title}\n"
        f"Description: {form.description}\n\n"
        f"Identity data:\n{persona}\n\n"
        f"Fields:\n" + "\n".join(field_lines)
    )
    reply = llm.complete(system, user)
    parsed = extract_json(reply)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object, got: {type(parsed).__name__}")
    return {str(k): v for k, v in parsed.items() if v is not None}


def _identity_prompt(identity: dict) -> str:
    lines = [f"- {k}: {v}" for k, v in identity.items() if k != "extras" and v]
    for extra in identity.get("extras", []):
        matches = ", ".join(f"'{m}'" for m in extra.get("match", []))
        lines.append(f"- question mentioning {matches}: {extra.get('value')}")
    return "\n".join(lines) or "(no identity data provided)"


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


# --------------------------------------------------------------------------- #
# Identity -> value resolution

def _extras_for(title: str, identity: dict) -> str | None:
    lowered = title.lower()
    for extra in identity.get("extras", []):
        for keyword in extra.get("match", []):
            if str(keyword).lower() in lowered:
                return str(extra.get("value", ""))
    return None


def _heuristic_value(field: FormField, identity: dict, allow_placeholder: bool = True) -> str | list[str] | None:
    title = field.title.lower()
    # Types we couldn't map (date/time pickers etc.) degrade to free-text.
    ftype = "short_answer" if field.type.startswith("unknown_type_") else field.type

    if ftype in _CHOICE_TYPES and field.options:
        return field.options[0]
    if ftype == "checkboxes" and field.options:
        return [field.options[0]]

    # Explicit extras win over keyword guesses.
    if (extra := _extras_for(field.title, identity)) is not None:
        return extra

    if field.input_type == "email" or "email" in title:
        return identity.get("email") or ("testy.mctestface@example.com" if allow_placeholder else None)
    if field.input_type == "tel" or "phone" in title or "mobile" in title:
        return identity.get("phone") or ("+15550000001" if allow_placeholder else None)
    if field.input_type == "number":
        return "1" if allow_placeholder else None
    if field.input_type == "date" or "date" in title:
        return "2026-01-01" if allow_placeholder else None
    if field.input_type == "time":
        return "12:00" if allow_placeholder else None
    if field.input_type == "url":
        return "https://example.com" if allow_placeholder else None
    if "name" in title:
        return identity.get("name") or ("Testy McTestface" if allow_placeholder else None)
    if any(word in title for word in ("organization", "organisation", "company", "employer", "school", "team")):
        return identity.get("organization") or ("Automated Testing Co" if allow_placeholder else None)
    if ftype == "paragraph":
        return "Automated response - please ignore." if allow_placeholder else None
    if ftype == "short_answer" and allow_placeholder:
        return "Automated response"
    return None


def is_answerable(field: FormField, identity: dict) -> bool:
    """Can we produce a real value for this field (placeholders don't count)?"""
    if field.type in _CHOICE_TYPES or field.type == "checkboxes":
        return bool(field.options)
    return _heuristic_value(field, identity, allow_placeholder=False) is not None


def value_source(field: FormField, identity: dict) -> str:
    """Where a field's answer would come from - for the check-requirements report."""
    if field.type in _CHOICE_TYPES or field.type == "checkboxes":
        return f"form option: {field.options[0]!r}" if field.options else "NO OPTIONS"
    if (extra := _extras_for(field.title, identity)) is not None:
        return f"identity extra: {extra!r}"
    title = field.title.lower()
    if field.input_type == "email" or "email" in title:
        return f"identity email: {identity.get('email') or 'MISSING'}"
    if field.input_type == "tel" or "phone" in title or "mobile" in title:
        return f"identity phone: {identity.get('phone') or 'MISSING'}"
    if "name" in title:
        return f"identity name: {identity.get('name') or 'MISSING'}"
    if any(word in title for word in ("organization", "organisation", "company", "employer", "school", "team")):
        return f"identity organization: {identity.get('organization') or 'MISSING'}"
    return "UNKNOWN (would need a guess)"