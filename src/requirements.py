"""Requirements document loader (markdown description).

The requirements.md file is a natural-language description of who the
user is, what forms they fill, and any constraints. The LLM reads
this document to decide if a form qualifies and what values to use.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_requirements(path: Path | None = None) -> str | None:
    """Load requirements.md as a plain string. Returns None if missing."""
    p = path or PROJECT_ROOT / "requirements.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def requirements_prompt(requirements_text: str) -> str:
    """Build a prompt fragment from the requirements document."""
    return (
        "=== FORM REQUIREMENTS (from requirements.md) ===\n"
        f"{requirements_text}\n"
        "=== END REQUIREMENTS ===\n"
    )


def evaluate_form_prompt(requirements_text: str, form_title: str, fields: list[dict]) -> str:
    """Build a prompt asking the LLM whether the form qualifies.

    The LLM should include [[YES]] or [[NO]] somewhere in its reply.
    """
    field_lines = []
    for f in fields:
        opts = f" options={f.get('options', [])}" if f.get("options") else ""
        field_lines.append(
            f"- {f['entry_id']} | type={f['type']} | required={f.get('required', False)}"
            f" | title={f.get('title', '')!r}{opts}"
        )
    return (
        requirements_prompt(requirements_text)
        + "Decide if this form qualifies based on the requirements above.\n"
        "You may think it through step by step first.\n"
        "Then, on the final line of your reply, write exactly one marker:\n"
        "  [[YES]] if the form qualifies\n"
        "  [[NO]] if it does not\n"
        "The final line must contain the marker and nothing else after it.\n"
        f"Form title: {form_title}\n"
        f"Fields:\n" + "\n".join(field_lines)
    )


def parse_gate_reply(reply: str) -> tuple[bool, str]:
    """Parse a gate reply that contains [[YES]] or [[NO]] markers.

    Returns (qualifies, reason).
    Raises ValueError if neither marker is found.
    """
    reply_upper = reply.upper()
    yes_marker = "[[YES]]"
    no_marker = "[[NO]]"

    has_yes = yes_marker in reply_upper
    has_no = no_marker in reply_upper

    if has_yes and not has_no:
        reason = _extract_gate_reason(reply, yes_marker)
        return True, reason
    if has_no and not has_yes:
        reason = _extract_gate_reason(reply, no_marker)
        return False, reason
    if has_yes and has_no:
        # Both present — the verdict is the final marker, since reasoning may
        # mention markers hypothetically before concluding.
        last_yes = reply_upper.rindex(yes_marker)
        last_no = reply_upper.rindex(no_marker)
        if last_yes > last_no:
            reason = _extract_gate_reason(reply, yes_marker)
            return True, reason
        reason = _extract_gate_reason(reply, no_marker)
        return False, reason

    raise ValueError(
        f"Gate reply must contain [[YES]] or [[NO]], got: {reply[:200]!r}"
    )


def _extract_gate_reason(reply: str, marker: str) -> str:
    """Extract the text after the marker as the reason."""
    idx = reply.upper().index(marker.upper()) + len(marker)
    return reply[idx:].strip().lstrip("-").strip().lstrip(":").strip()


def fill_form_prompt(requirements_text: str, form_title: str, fields: list[dict]) -> str:
    """Build a prompt asking the LLM to generate test values for the form."""
    field_lines = []
    for f in fields:
        opts = f" options={f.get('options', [])}" if f.get("options") else ""
        req = " [required]" if f.get("required") else ""
        field_lines.append(
            f"- {f['entry_id']}{req} | type={f['type']} | title={f.get('title', '')!r}{opts}"
        )
    return (
        requirements_prompt(requirements_text)
        + "Generate test values for this form based on the requirements above.\n"
        "Rules:\n"
        "- Reply with ONLY a JSON object mapping entry ids to values\n"
        "- For choice fields, pick a real option from the list\n"
        "- For text fields, generate realistic fake data\n"
        "- Do NOT invent options that don't exist\n"
        "- Do NOT write placeholder text like 'Automated test response'\n"
        f"Form title: {form_title}\n"
        f"Fields:\n" + "\n".join(field_lines)
    )