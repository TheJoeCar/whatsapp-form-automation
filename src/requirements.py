"""The form requirements document: identity data + rules for which forms qualify.

`requirements.yaml` is enforced by the pipeline: forms that fail any rule are
skipped with a logged reason. The identity section feeds the form filler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .forms.filler import is_answerable
from .forms.parser import GoogleForm


@dataclass
class Rules:
    title_must_include: list[str] = field(default_factory=list)
    title_must_not_include: list[str] = field(default_factory=list)
    min_fields: int = 1
    max_fields: int = 30
    disallowed_types: list[str] = field(default_factory=list)
    disallowed_keywords: list[str] = field(default_factory=list)
    required_all_answerable: bool = True
    fill_optional: bool = False
    never_guess: bool = True

    def as_dict(self) -> dict:
        return {
            "title_must_include": self.title_must_include,
            "title_must_not_include": self.title_must_not_include,
            "min_fields": self.min_fields,
            "max_fields": self.max_fields,
            "disallowed_types": self.disallowed_types,
            "disallowed_keywords": self.disallowed_keywords,
            "required_all_answerable": self.required_all_answerable,
            "fill_optional": self.fill_optional,
            "never_guess": self.never_guess,
        }


@dataclass
class Identity:
    name: str = ""
    email: str = ""
    phone: str = ""
    organization: str = ""
    extras: list[dict] = field(default_factory=list)  # [{"match": [...], "value": str}]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "organization": self.organization,
            "extras": self.extras,
        }


@dataclass
class Requirements:
    identity: Identity
    rules: Rules
    source: Path | None = None

    def as_fill_args(self) -> tuple[dict, dict]:
        """(identity_dict, rules_dict) for the filler."""
        return self.identity.as_dict(), self.rules.as_dict()


def load_requirements(path: Path | str | None) -> Requirements | None:
    """Load requirements.yaml; None if the path is missing."""
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        return None

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    identity_raw = raw.get("identity") or {}
    identity = Identity(
        name=str(identity_raw.get("name", "") or ""),
        email=str(identity_raw.get("email", "") or ""),
        phone=str(identity_raw.get("phone", "") or ""),
        organization=str(identity_raw.get("organization", "") or ""),
        extras=identity_raw.get("extras") or [],
    )

    rules_raw = raw.get("rules") or {}
    rules = Rules(
        title_must_include=list(rules_raw.get("title_must_include") or []),
        title_must_not_include=list(rules_raw.get("title_must_not_include") or []),
        min_fields=int(rules_raw.get("min_fields", 1)),
        max_fields=int(rules_raw.get("max_fields", 30)),
        disallowed_types=list(rules_raw.get("disallowed_types") or []),
        disallowed_keywords=list(rules_raw.get("disallowed_keywords") or []),
        required_all_answerable=bool(rules_raw.get("required_all_answerable", True)),
        fill_optional=bool(rules_raw.get("fill_optional", False)),
        never_guess=bool(rules_raw.get("never_guess", True)),
    )

    return Requirements(identity=identity, rules=rules, source=path)


def evaluate_form(form: GoogleForm, rules: Rules, identity: dict) -> list[str]:
    """Check a parsed form against the requirements rules.

    Returns a list of violation reasons; empty list means the form qualifies.
    """
    reasons: list[str] = []
    title_l = (form.title or "").lower()

    if rules.title_must_include and not any(k.lower() in title_l for k in rules.title_must_include):
        reasons.append(f"title {form.title!r} does not include any of {rules.title_must_include}")

    excluded_in_title = [k for k in rules.title_must_not_include if k.lower() in title_l]
    if excluded_in_title:
        reasons.append(f"title contains excluded keyword(s): {excluded_in_title}")

    if len(form.fields) < rules.min_fields:
        reasons.append(f"only {len(form.fields)} field(s), minimum is {rules.min_fields}")
    if len(form.fields) > rules.max_fields:
        reasons.append(f"{len(form.fields)} fields exceed the maximum of {rules.max_fields}")

    disallowed_types = set(rules.disallowed_types)
    if disallowed_types:
        present = sorted({f.type for f in form.fields} & disallowed_types)
        if present:
            reasons.append(f"disallowed question type(s) present: {present}")

    for field in form.fields:
        hit = [k for k in rules.disallowed_keywords if k.lower() in field.title.lower()]
        if hit:
            reasons.append(f"question {field.entry_id} ({field.title!r}) contains excluded keyword(s): {hit}")
            break

    if rules.required_all_answerable:
        unanswerable = [
            f"{f.entry_id} ({f.title or 'untitled'})"
            for f in form.fields
            if f.required and not is_answerable(f, identity)
        ]
        if unanswerable:
            reasons.append("required field(s) not answerable from identity: " + ", ".join(unanswerable))

    return reasons