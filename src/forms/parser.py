"""Google Forms URL handling and structure parsing.

Modern Google Forms pages render questions from JSON "data-params" attributes
and hidden `entry.NN_sentinel` inputs - there are no visible entry inputs.
Legacy pages have real `<input name="entry.NN">` elements. Both carry the same
question JSON shape (`[itemId, title, null, type, [[entryId, options, required,
...]], ...]`), so one extractor handles both:

  1. `data-params` attributes (primary, modern)
  2. `FB_PUBLIC_LOAD_DATA_[1][1]` items (fallback)
  3. DOM entry inputs (legacy, also supplies input types)

If parsing looks wrong, run `python -m src.cli parse-form <url>` and inspect.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

ENTRY_NAME_RE = re.compile(r"^entry\.\d+$")
ENTRY_ID_RE = re.compile(r"^(?:entry\.)?(\d{6,})$")
FORM_ID_RE = re.compile(r"/forms/(?:u/\d+/)?d(?:/e)?/([A-Za-z0-9_-]{10,})")
GOOGLE_FORMS_HOST_RE = re.compile(r"(docs\.google\.com/forms/|forms\.gle/)")

# Question type ids as used in data-params / FB_PUBLIC_LOAD_DATA_ items.
TYPE_NAMES = {
    0: "short_answer",
    1: "paragraph",
    2: "multiple_choice",
    3: "dropdown",
    4: "checkboxes",
    5: "linear_scale",
}


@dataclass
class FormField:
    entry_id: str  # e.g. "entry.123456789"
    title: str = ""
    type: str = "short_answer"  # short_answer|paragraph|multiple_choice|checkboxes|dropdown|linear_scale
    required: bool = False
    options: list[str] = field(default_factory=list)
    input_type: str = ""  # text|email|tel|url|number|date|time|radio|checkbox
    note: str = ""  # parser diagnostics


@dataclass
class GoogleForm:
    title: str = ""
    description: str = ""
    url: str = ""  # viewform URL (canonical, for parsing)
    submit_url: str = ""  # formResponse URL
    fbzx: str | None = None
    fields: list[FormField] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Form: {self.title or '(untitled)'}", f"URL: {self.url}"]
        if self.description:
            lines.append(f"Description: {self.description}")
        lines.append(f"Fields ({len(self.fields)}):")
        for f in self.fields:
            req = " [required]" if f.required else ""
            opts = f" options={f.options}" if f.options else ""
            note = f" ({f.note})" if f.note else ""
            lines.append(f"  {f.entry_id} | {f.type} | {f.title!r}{req}{opts}{note}")
        return "\n".join(lines)


def is_google_forms_url(url: str) -> bool:
    return bool(GOOGLE_FORMS_HOST_RE.search(url))


def resolve_url(url: str, timeout: float = 30.0) -> str:
    """Follow redirects (forms.gle short links) to the final docs.google.com URL."""
    if "forms.gle" not in url:
        return url
    resp = requests.head(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT})
    if resp.status_code >= 400 or "docs.google.com/forms" not in resp.url:
        # Some short links refuse HEAD; retry with GET.
        resp = requests.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT})
    return resp.url


def form_id_from_url(url: str) -> str | None:
    match = FORM_ID_RE.search(url)
    return match.group(1) if match else None


def canonical_urls(url: str) -> tuple[str, str]:
    """Return (viewform_url, formResponse_url) preserving the /d/ vs /d/e/ path shape."""
    match = re.search(r"/forms/((?:u/\d+/)?d(?:/e)?)/([A-Za-z0-9_-]{10,})", url)
    if not match:
        raise ValueError(f"Not a recognisable Google Forms URL: {url}")
    shape, form_id = match.groups()
    return (
        f"https://docs.google.com/forms/{shape}/{form_id}/viewform",
        f"https://docs.google.com/forms/{shape}/{form_id}/formResponse",
    )


def fetch_form_html(url: str, timeout: float = 30.0) -> tuple[str, str]:
    """Fetch a form page; returns (html, final_viewform_url)."""
    viewform, _ = canonical_urls(resolve_url(url))
    resp = requests.get(viewform, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text, viewform


def parse_form_html(html: str, url: str = "") -> GoogleForm:
    soup = BeautifulSoup(html, "lxml")
    _, submit_url = canonical_urls(url)

    form = GoogleForm(url=url, submit_url=submit_url)
    form.title = _extract_title(soup, html)
    form.description = _extract_description(soup)
    fbzx_input = soup.find("input", attrs={"name": "fbzx"})
    form.fbzx = fbzx_input.get("value") if fbzx_input else None

    questions = _extract_questions(html)  # entry_id -> metadata (modern + FB)

    # Legacy path: real entry inputs in the DOM.
    dom_fields: dict[str, FormField] = {}
    groups: dict[str, list] = {}
    for tag in soup.find_all(["input", "textarea", "select"]):
        name = tag.get("name") or ""
        if ENTRY_NAME_RE.match(name):
            groups.setdefault(name, []).append(tag)
    for entry_id, tags in groups.items():
        dom_fields[entry_id] = _build_field_from_dom(entry_id, tags, soup)

    # Merge: metadata enriches DOM fields; metadata-only fields become fields.
    fields: list[FormField] = []
    seen: set[str] = set()
    for entry_id, meta in questions.items():
        canonical_id = _canonical_entry_id(entry_id)
        if canonical_id in seen:
            continue
        dom_field = dom_fields.get(canonical_id)
        fields.append(_merge_field(canonical_id, meta, dom_field))
        seen.add(canonical_id)
    for entry_id, dom_field in dom_fields.items():
        if entry_id not in seen:
            fields.append(dom_field)

    form.fields = fields
    return form


def _canonical_entry_id(raw: str) -> str:
    match = ENTRY_ID_RE.match(str(raw))
    return f"entry.{match.group(1)}" if match else str(raw)


# --------------------------------------------------------------------------- #
# Question extraction (data-params + FB_PUBLIC_LOAD_DATA_)

def _extract_questions(html: str) -> dict[str, dict]:
    """entry_id (numeric str) -> {title, type, options, required}."""
    questions: dict[str, dict] = {}

    # 1. data-params attributes (modern rendering).
    for raw in re.findall(r'data-params="([^"]+)"', html):
        text = html_module.unescape(raw).lstrip(".%@")
        for doc in _iter_json_docs(text):
            meta = _question_from_doc(doc)
            if meta:
                for entry_id, info in meta.items():
                    questions.setdefault(entry_id, info)

    # 2. FB_PUBLIC_LOAD_DATA_ items (fallback / legacy).
    fb = _extract_fb_json(html)
    if fb:
        try:
            items = fb[1][1]
        except (IndexError, TypeError):
            items = []
        if isinstance(items, list):
            for item in items:
                meta = _question_from_doc(item)
                if meta:
                    for entry_id, info in meta.items():
                        questions.setdefault(entry_id, info)
    return questions


def _iter_json_docs(text: str):
    """Yield concatenated JSON values, skipping the odd separators Google adds."""
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(text):
        try:
            doc, end = decoder.raw_decode(text, pos)
            yield doc
            pos = end
        except json.JSONDecodeError:
            pos += 1


def _question_from_doc(doc) -> dict[str, dict] | None:
    """Match [itemId, title, null, type, [[entryId, options, required, ...]], ...]."""
    if not (
        isinstance(doc, list)
        and len(doc) >= 5
        and isinstance(doc[0], int)
        and isinstance(doc[1], str)
        and isinstance(doc[3], int)
        and isinstance(doc[4], list)
        and doc[4]
        and isinstance(doc[4][0], list)
    ):
        return None

    out: dict[str, dict] = {}
    for wrapper in doc[4]:
        if not (isinstance(wrapper, list) and wrapper and isinstance(wrapper[0], (int, str))):
            continue
        entry_id = str(wrapper[0])
        if not re.fullmatch(r"\d{6,}", entry_id):
            continue
        info: dict = {
            "title": doc[1],
            "type": TYPE_NAMES.get(doc[3], f"unknown_type_{doc[3]}"),
            "options": [],
            "required": bool(wrapper[2]) if len(wrapper) > 2 and isinstance(wrapper[2], (bool, int)) else None,
        }
        if len(wrapper) > 1 and isinstance(wrapper[1], list):
            for option in wrapper[1]:
                if isinstance(option, list) and option and isinstance(option[0], str):
                    info["options"].append(option[0])
        out[entry_id] = info
    return out or None


def _extract_fb_json(html: str) -> list | None:
    match = re.search(r"FB_PUBLIC_LOAD_DATA_\s*=\s*(.*?);\s*</script>", html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Field construction

def _merge_field(entry_id: str, meta: dict, dom_field: FormField | None) -> FormField:
    """Metadata-first field; DOM input types are additive."""
    note = ""
    if dom_field is not None:
        field_type = dom_field.type if dom_field.type != "short_answer" else meta.get("type", dom_field.type)
        if meta.get("type", "").startswith("unknown_type_"):
            field_type = dom_field.type or meta["type"]
            note = "type from DOM"
        return FormField(
            entry_id=entry_id,
            title=meta.get("title") or dom_field.title,
            type=field_type,
            required=bool(meta.get("required")) or dom_field.required,
            options=meta.get("options") or dom_field.options,
            input_type=dom_field.input_type,
            note=note,
        )
    return FormField(
        entry_id=entry_id,
        title=meta.get("title", ""),
        type=meta.get("type", "short_answer"),
        required=bool(meta.get("required")),
        options=meta.get("options", []),
        note="from data-params/FB metadata",
    )


def _build_field_from_dom(entry_id: str, tags: list, soup: BeautifulSoup) -> FormField:
    first = tags[0]
    tag_name = first.name
    input_type = (first.get("type") or "").lower()

    options: list[str] = []
    ftype = "short_answer"

    if tag_name == "select":
        ftype = "dropdown"
        options = [
            o.get_text(strip=True)
            for o in first.find_all("option")
            if o.get_text(strip=True) and o.get("value") != ""
        ]
    elif tag_name == "textarea":
        ftype = "paragraph"
    elif tag_name == "input" and input_type == "radio":
        options = [o for o in (_label_for_input(tag, soup) for tag in tags) if o]
        ftype = "linear_scale" if options and all(o.isdigit() for o in options) else "multiple_choice"
    elif tag_name == "input" and input_type == "checkbox":
        options = [o for o in (_label_for_input(tag, soup) for tag in tags) if o]
        ftype = "checkboxes"

    title, note = _title_for_field(first, soup)

    required = any(tag.has_attr("required") or tag.get("aria-required") == "true" for tag in tags)

    return FormField(
        entry_id=entry_id,
        title=title,
        type=ftype,
        required=required,
        options=options,
        input_type=input_type,
        note=note,
    )


def _label_for_input(tag, soup: BeautifulSoup) -> str:
    """Best-effort option label for a radio/checkbox input (legacy pages)."""
    input_id = tag.get("id")
    if input_id:
        label = soup.find("label", attrs={"for": input_id})
        if label:
            text = label.get_text(" ", strip=True)
            if text:
                return text
    text = _text_for_labelledby(tag, soup)
    if text:
        return text
    parent_label = tag.find_parent("label")
    if parent_label:
        return parent_label.get_text(" ", strip=True)
    sibling = tag.find_next("span")
    return sibling.get_text(" ", strip=True) if sibling else ""


def _text_for_labelledby(tag, soup: BeautifulSoup) -> str:
    ids = (tag.get("aria-labelledby") or "").split()
    texts = []
    for element_id in ids:
        el = soup.find(attrs={"id": element_id})
        if el:
            texts.append(el.get_text(" ", strip=True))
    return " ".join(t for t in texts if t)


def _title_for_field(tag, soup: BeautifulSoup) -> tuple[str, str]:
    """Question title from DOM heuristics (legacy pages without metadata)."""
    container = tag.find_parent(attrs={"role": "listitem"}) or tag
    heading = container.find(attrs={"role": "heading"})
    if heading:
        return heading.get_text(" ", strip=True), "title from DOM heading"
    text = _text_for_labelledby(tag, soup)
    if text:
        return text, "title from aria-labelledby"
    return "", "title not found - review with parse-form"


# --------------------------------------------------------------------------- #
# Form-level metadata

def _extract_title(soup: BeautifulSoup, html: str = "") -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return re.sub(r"\s*[-–]\s*Google Forms\s*$", "", soup.title.string).strip()
    fb = _extract_fb_json(html)
    if fb:
        try:
            if isinstance(fb[1][0], str):
                return fb[1][0]
        except (IndexError, TypeError):
            pass
    return ""


def _extract_description(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:description"})
    if og and og.get("content"):
        return og["content"].strip()
    return ""