"""Tests for the markdown-based requirements document and LLM-driven filling."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forms.filler import fill_form  # noqa: E402
from src.forms.parser import FormField, GoogleForm, parse_form_html  # noqa: E402
from src.requirements import (
    load_requirements,
    evaluate_form_prompt,
    fill_form_prompt,
    parse_gate_reply,
    requirements_prompt,
)  # noqa: E402

FORM_URL = "https://docs.google.com/forms/d/e/FAKEID123456/viewform"

SYNTHETIC_HTML = """
<html><head><title>Test Form - Google Forms</title>
<meta property="og:title" content="Test Form"></head><body>
<form>
<input name="fbzx" value="fbzx-token-123"/>
<div role="listitem"><div role="heading">What is your name?</div>
  <input type="text" name="entry.1111111111" required/></div>
<div role="listitem"><div role="heading">Your email?</div>
  <input type="text" name="entry.2222222222"/></div>
<div role="listitem"><div role="heading">Pick one</div>
  <input type="radio" name="entry.3333333333" id="r1" value="Yes"/><label for="r1">Yes</label>
  <input type="radio" name="entry.3333333333" id="r2" value="No"/><label for="r2">No</label></div>
<div role="listitem"><div role="heading">Choose fruit</div>
  <select name="entry.4444444444">
    <option value="">Choose</option>
    <option value="Apple">Apple</option>
    <option value="Banana">Banana</option>
  </select></div>
<div role="listitem"><div role="heading">Comments?</div>
  <textarea name="entry.5555555555"></textarea></div>
</form>
<script>var FB_PUBLIC_LOAD_DATA_ = [null,null,[]];</script>
</body></html>
"""

MODERN_HTML = """
<html><head><title>Modern Form</title></head><body>
<div data-params="%.@.[11111111,&quot;What is your name?&quot;,null,0,[[2222222222,null,true,null,null,null,0]],null,null,null,null,null,null,[null,&quot;What is your name?&quot;]]"></div>
<div data-params="%.@.[33333333,&quot;Pick one&quot;,null,2,[[4444444444,[[&quot;Yes&quot;,null,null,null,0],[&quot;No&quot;,null,null,null,0]],1,null,null,null,null,null,0]],null,null,null,null,null,null,[null,&quot;Pick one&quot;]]"></div>
<input type="hidden" name="entry.2222222222_sentinel"/>
<input type="hidden" name="entry.4444444444_sentinel"/>
</body></html>
"""

SAMPLE_REQUIREMENTS = """
## Who I Am (Test Persona)

- **Name:** Test User
- **Email:** test.user@example.com

## What Kinds of Forms I Fill

I mainly fill out event registration forms, surveys, and sign-up forms.
These typically ask about personal info, experience, and preferences.

## Rules

- Only fill forms I would realistically sign up for.
- Answer required questions first.
- Never write placeholder text into required fields.
"""


def test_parse_synthetic_form():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    assert form.title == "Test Form"
    by_entry = {f.entry_id: f for f in form.fields}
    assert set(by_entry) == {
        "entry.1111111111", "entry.2222222222", "entry.3333333333",
        "entry.4444444444", "entry.5555555555",
    }
    assert by_entry["entry.1111111111"].required is True
    assert by_entry["entry.3333333333"].type == "multiple_choice"
    assert by_entry["entry.4444444444"].type == "dropdown"


def test_parse_modern_data_params_form():
    form = parse_form_html(MODERN_HTML, url=FORM_URL)
    by_entry = {f.entry_id: f for f in form.fields}
    assert set(by_entry) == {"entry.2222222222", "entry.4444444444"}
    assert by_entry["entry.2222222222"].required is True
    assert by_entry["entry.4444444444"].type == "multiple_choice"


def test_parse_gate_reply_yes():
    qualifies, reason = parse_gate_reply("YES - this is an event registration form")
    assert qualifies is True
    assert reason == "this is an event registration form"


def test_parse_gate_reply_yes_with_dash():
    qualifies, reason = parse_gate_reply("YES - matches requirements")
    assert qualifies is True
    assert reason == "matches requirements"


def test_parse_gate_reply_no():
    qualifies, reason = parse_gate_reply("NO - not a form I would fill")
    assert qualifies is False
    assert reason == "not a form I would fill"


def test_parse_gate_reply_no_uppercase():
    qualifies, reason = parse_gate_reply("NO - irrelevant")
    assert qualifies is False


def test_parse_gate_reply_invalid():
    import pytest
    with pytest.raises(ValueError):
        parse_gate_reply("maybe? I'm not sure about this form.")


def test_parse_gate_reply_empty():
    import pytest
    with pytest.raises(ValueError):
        parse_gate_reply("")


def test_parse_gate_reply_case_insensitive():
    qualifies, reason = parse_gate_reply("yes - it qualifies")
    assert qualifies is True
    qualifies, reason = parse_gate_reply("no - it does not")
    assert qualifies is False


def test_requirements_prompt():
    prompt = requirements_prompt(SAMPLE_REQUIREMENTS)
    assert "=== FORM REQUIREMENTS" in prompt
    assert "event registration" in prompt.lower()
    assert "=== END REQUIREMENTS ===" in prompt


def test_evaluate_form_prompt():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    field_summaries = [
        {"entry_id": f.entry_id, "type": f.type, "required": f.required, "title": f.title, "options": f.options}
        for f in form.fields
    ]
    prompt = evaluate_form_prompt(SAMPLE_REQUIREMENTS, form.title or "", field_summaries)
    assert "event registration" in prompt.lower()
    assert "entry.1111111111" in prompt
    assert "YES" in prompt
    assert "NO" in prompt


def test_fill_form_prompt():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    field_summaries = [
        {"entry_id": f.entry_id, "type": f.type, "required": f.required, "title": f.title, "options": f.options}
        for f in form.fields
    ]
    prompt = fill_form_prompt(SAMPLE_REQUIREMENTS, form.title or "", field_summaries)
    assert "event registration" in prompt.lower()
    assert "entry.1111111111" in prompt


def test_filler_no_fallback_without_llm():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    values, warnings = fill_form(form, llm=None)
    # No fallback values — empty dict when LLM is unavailable
    assert values == {}
    assert any("no values" in w for w in warnings)


def test_filler_falls_back_when_whole_reply_invalid():
    class FakeLLM:
        def complete(self, system, user, max_tokens=1500):
            return '{"entry.3333333333": "Maybe"}'
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    values, warnings = fill_form(form, llm=FakeLLM())
    # entry.3333333333 is optional and "Maybe" is not a valid option,
    # so it gets dropped. No fallback to heuristics since LLM succeeded.
    assert "entry.3333333333" not in values
    assert any("not one of" in w for w in warnings)


def test_load_requirements_missing(tmp_path):
    assert load_requirements(tmp_path / "nope.md") is None


def test_load_requirements_file(tmp_path):
    doc = tmp_path / "requirements.md"
    doc.write_text(SAMPLE_REQUIREMENTS, encoding="utf-8")
    text = load_requirements(doc)
    assert text is not None
    assert "event registration" in text.lower()
    assert "Test User" in text