"""Tests for the requirements document: gate rules and strict filling."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forms.filler import FillAborted, fill_form, is_answerable, value_source  # noqa: E402
from src.forms.parser import FormField, GoogleForm  # noqa: E402
from src.requirements import Rules, evaluate_form, load_requirements  # noqa: E402


def make_form(title="Signup Form", fields=None):
    if fields is None:
        fields = [
            FormField(entry_id="entry.1", title="Your name?", type="short_answer", required=True),
            FormField(entry_id="entry.2", title="Your email?", type="short_answer", required=True),
            FormField(
                entry_id="entry.3",
                title="Preferred time",
                type="multiple_choice",
                options=["Morning", "Afternoon"],
            ),
        ]
    return GoogleForm(
        title=title,
        url="https://docs.google.com/forms/d/e/FAKEID123456/viewform",
        submit_url="https://docs.google.com/forms/d/e/FAKEID123456/formResponse",
        fields=fields,
    )


IDENTITY = {
    "name": "Joe Sample",
    "email": "joe.sample@example.com",
    "phone": "+15551234567",
    "organization": "Sample Co",
    "extras": [{"match": ["age", "how old"], "value": "34"}],
}


def test_passes_when_everything_matches():
    reasons = evaluate_form(make_form(), Rules(), IDENTITY)
    assert reasons == []


def test_title_must_include():
    rules = Rules(title_must_include=["survey", "poll"])
    reasons = evaluate_form(make_form(title="Registration Form"), rules, IDENTITY)
    assert any("does not include" in r for r in reasons)
    assert evaluate_form(make_form(title="Survey about coffee"), rules, IDENTITY) == []


def test_title_must_not_include():
    rules = Rules(title_must_not_include=["giveaway"])
    reasons = evaluate_form(make_form(title="Giveaway entry"), rules, IDENTITY)
    assert any("excluded keyword" in r for r in reasons)


def test_field_bounds():
    assert any("minimum" in r for r in evaluate_form(make_form(), Rules(min_fields=10), IDENTITY))
    assert any("exceed" in r for r in evaluate_form(make_form(), Rules(max_fields=2), IDENTITY))


def test_disallowed_types():
    rules = Rules(disallowed_types=["checkboxes"])
    assert evaluate_form(make_form(), rules, IDENTITY) == []
    fields = [FormField(entry_id="entry.9", title="Pick many", type="checkboxes", options=["a"])]
    reasons = evaluate_form(make_form(fields=fields), rules, IDENTITY)
    assert any("checkboxes" in r for r in reasons)


def test_disallowed_keywords_in_questions():
    rules = Rules(disallowed_keywords=["password"])
    fields = [FormField(entry_id="entry.9", title="Choose a password", type="short_answer", required=True)]
    reasons = evaluate_form(make_form(fields=fields), rules, IDENTITY)
    assert any("password" in r for r in reasons)


def test_required_all_answerable():
    fields = [
        FormField(entry_id="entry.1", title="Your name?", type="short_answer", required=True),
        FormField(entry_id="entry.2", title="Favourite colour?", type="short_answer", required=True),
    ]
    form = make_form(fields=fields)
    rules = Rules()
    reasons = evaluate_form(form, rules, IDENTITY)
    assert any("not answerable" in r and "entry.2" in r for r in reasons)
    # With an extra covering the colour question, it passes.
    identity = dict(IDENTITY, extras=IDENTITY["extras"] + [{"match": ["colour", "color"], "value": "blue"}])
    assert evaluate_form(form, rules, identity) == []


def test_answerable_and_value_source():
    email_field = FormField(entry_id="entry.1", title="Your email?", type="short_answer", required=True)
    age_field = FormField(entry_id="entry.2", title="How old are you?", type="short_answer", required=True)
    unknown = FormField(entry_id="entry.3", title="Favourite colour?", type="short_answer", required=True)
    assert is_answerable(email_field, IDENTITY)
    assert is_answerable(age_field, IDENTITY)
    assert not is_answerable(unknown, IDENTITY)
    assert "joe.sample@example.com" in value_source(email_field, IDENTITY)
    assert "34" in value_source(age_field, IDENTITY)
    assert "UNKNOWN" in value_source(unknown, IDENTITY)


def test_extras_matching():
    age_field = FormField(entry_id="entry.1", title="How old are you?", type="short_answer")
    values, _warnings = fill_form(make_form(fields=[age_field]), llm=None, identity=IDENTITY)
    assert values["entry.1"] == "34"


def test_strict_mode_skips_optional():
    form = make_form()
    values, warnings = fill_form(form, llm=None, identity=IDENTITY, rules={"fill_optional": False})
    assert values["entry.1"] == "Joe Sample"
    assert values["entry.2"] == "joe.sample@example.com"
    assert "entry.3" not in values  # optional choice skipped
    assert any("optional field" in w for w in warnings)


def test_never_guess_aborts_instead_of_placeholder():
    fields = [FormField(entry_id="entry.1", title="Favourite colour?", type="short_answer", required=True)]
    form = make_form(fields=fields)
    with pytest.raises(FillAborted) as excinfo:
        fill_form(form, llm=None, identity=IDENTITY, rules={"never_guess": True})
    assert "entry.1" in str(excinfo.value)


def test_strict_mode_uses_choice_options():
    # Choice fields are answerable from their own options even in strict mode.
    form = make_form()
    values, _warnings = fill_form(form, llm=None, identity=IDENTITY, rules={"fill_optional": True})
    assert values["entry.3"] == "Morning"


def test_load_requirements_file(tmp_path):
    doc = tmp_path / "requirements.yaml"
    doc.write_text(
        """
identity:
  name: "Jane Doe"
  email: "jane@example.com"
  extras:
    - match: ["team"]
      value: "Red team"
rules:
  max_fields: 5
  disallowed_types: ["checkboxes"]
  fill_optional: true
  never_guess: false
""",
        encoding="utf-8",
    )
    reqs = load_requirements(doc)
    assert reqs is not None
    identity, rules = reqs.as_fill_args()
    assert identity["name"] == "Jane Doe"
    assert identity["extras"][0]["value"] == "Red team"
    assert rules["max_fields"] == 5
    assert rules["fill_optional"] is True
    assert rules["never_guess"] is False


def test_load_requirements_missing(tmp_path):
    assert load_requirements(tmp_path / "nope.yaml") is None