"""Requirements document loader (markdown description).

The requirements.md file is a natural-language description of who the
user is, what forms they fill, and any constraints. The LLM reads
this document to decide if a form qualifies and what values to use.
"""

from __future__ import annotations

from pathlib import Path

from .llm.client import extract_json

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
        "You may think it through before answering.\n"
        'Reply with ONLY a JSON object: {"qualifies": true or false, "reason": "<short reason>"}\n'
        f"Form title: {form_title}\n"
        f"Fields:\n" + "\n".join(field_lines)
    )


def parse_gate_reply(reply: str) -> tuple[bool, str]:
    """Parse a gate reply containing {"qualifies": bool, "reason": str}.

    Tolerates prose around the JSON and string values like "yes"/"no".
    Returns (qualifies, reason). Raises ValueError if unparseable.
    """
    data = extract_json(reply)
    if not isinstance(data, dict):
        raise ValueError(f"Gate reply is not a JSON object: {reply[:200]!r}")

    raw = data.get("qualifies")
    if isinstance(raw, bool):
        qualifies = raw
    elif isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"yes", "true", "y", "1"}:
            qualifies = True
        elif lowered in {"no", "false", "n", "0"}:
            qualifies = False
        else:
            raise ValueError(f"Gate 'qualifies' is not a boolean: {raw!r}")
    else:
        raise ValueError(f"Gate reply missing boolean 'qualifies': {reply[:200]!r}")

    reason = str(data.get("reason", "")).strip()
    return qualifies, reason


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