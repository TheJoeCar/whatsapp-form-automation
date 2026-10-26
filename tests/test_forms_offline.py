"""Offline tests: synthetic form parsing, LLM JSON extraction, filler validation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forms.parser import parse_form_html  # noqa: E402
from src.forms.filler import fill_form  # noqa: E402
from src.llm.client import extract_json  # noqa: E402

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


class FakeLLM:
    """Returns a canned completion - enough to exercise the filler path."""

    def __init__(self, reply: str):
        self.reply = reply
        self.received = None

    def complete(self, system, user, max_tokens=1500):
        self.received = user
        return self.reply


def test_parse_synthetic_form():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    assert form.title == "Test Form"
    assert form.fbzx == "fbzx-token-123"
    assert form.submit_url.endswith("/formResponse")
    by_entry = {f.entry_id: f for f in form.fields}

    assert set(by_entry) == {
        "entry.1111111111", "entry.2222222222", "entry.3333333333",
        "entry.4444444444", "entry.5555555555",
    }
    name_field = by_entry["entry.1111111111"]
    assert name_field.title == "What is your name?"
    assert name_field.required is True
    assert name_field.type == "short_answer"

    choice = by_entry["entry.3333333333"]
    assert choice.type == "multiple_choice"
    assert choice.options == ["Yes", "No"]

    dropdown = by_entry["entry.4444444444"]
    assert dropdown.type == "dropdown"
    assert dropdown.options == ["Apple", "Banana"]  # empty-value placeholder excluded

    assert by_entry["entry.5555555555"].type == "paragraph"


def test_parse_modern_data_params_form():
    """Modern pages: questions live in data-params JSON, not entry inputs."""
    form = parse_form_html(MODERN_HTML, url=FORM_URL)
    by_entry = {f.entry_id: f for f in form.fields}

    assert set(by_entry) == {"entry.2222222222", "entry.4444444444"}
    name_field = by_entry["entry.2222222222"]
    assert name_field.title == "What is your name?"
    assert name_field.type == "short_answer"
    assert name_field.required is True

    choice = by_entry["entry.4444444444"]
    assert choice.title == "Pick one"
    assert choice.type == "multiple_choice"
    assert choice.options == ["Yes", "No"]
    assert choice.required is True


def test_extract_json_with_prose():
    assert extract_json('Sure! Here you go:\n{"entry.1": "ok"}\nHope that helps') == {"entry.1": "ok"}


def test_filler_with_fake_llm():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    reply = (
        '{"entry.1111111111": "Testy McTestface", '
        '"entry.2222222222": "testy@example.com", '
        '"entry.3333333333": "No", '
        '"entry.4444444444": "Banana", '
        '"entry.5555555555": "A comment", '
        '"entry.9999999999": "bogus"}'
    )
    values, warnings = fill_form(form, llm=FakeLLM(reply))
    assert values["entry.1111111111"] == "Testy McTestface"
    assert values["entry.3333333333"] == "No"
    assert values["entry.4444444444"] == "Banana"
    assert "entry.9999999999" not in values  # unknown entry dropped
    assert any("not a field" in w for w in warnings)


def test_filler_falls_back_when_whole_reply_invalid():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    reply = '{"entry.3333333333": "Maybe"}'  # not a real option
    values, warnings = fill_form(form, llm=FakeLLM(reply))
    # nothing valid survived -> full heuristic fallback kicks in
    assert values["entry.3333333333"] == "Yes"
    assert any("not one of" in w for w in warnings)
    assert any("heuristics" in w for w in warnings)
    # required short_answer is still filled
    assert values["entry.1111111111"]


def test_filler_partial_llm_success():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    reply = '{"entry.1111111111": "Testy", "entry.3333333333": "Bogus"}'
    values, warnings = fill_form(form, llm=FakeLLM(reply))
    assert values["entry.1111111111"] == "Testy"  # valid LLM value kept
    assert "entry.3333333333" not in values  # invalid option dropped, optional -> stays empty
    assert any("not one of" in w for w in warnings)


def test_filler_heuristic_fallback_without_llm():
    form = parse_form_html(SYNTHETIC_HTML, url=FORM_URL)
    values, _warnings = fill_form(form, llm=None)
    by_entry = {f.entry_id: f for f in form.fields}
    assert "email" in values["entry.2222222222"] or values["entry.2222222222"]
    assert values["entry.3333333333"] == "Yes"  # first option
    assert values["entry.4444444444"] == "Apple"
    assert by_entry["entry.1111111111"].required and values["entry.1111111111"]