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

    The LLM should reply with exactly one line:
      YES - <reason>
    or
      NO - <reason>
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
        "Reply with exactly one line:\n"
        "  YES - <reason>  (if the form matches the requirements)\n"
        "  NO - <reason>   (if it does not match)\n"
        f"Form title: {form_title}\n"
        f"Fields:\n" + "\n".join(field_lines)
    )


def parse_gate_reply(reply: str) -> tuple[bool, str]:
    """Parse a gate reply in YES/NO format.

    Returns (qualifies, reason).
    Raises ValueError if the reply doesn't start with YES or NO.
    """
    lines = reply.strip().splitlines()
    if not lines or not lines[0].strip():
        raise ValueError("Gate reply is empty")
    first_line = lines[0].strip()
    if first_line.upper().startswith("YES"):
        reason = first_line[3:].strip().lstrip("-").strip()
        return True, reason
    if first_line.upper().startswith("NO"):
        reason = first_line[2:].strip().lstrip("-").strip()
        return False, reason
    raise ValueError(
        f"Gate reply must start with YES or NO, got: {first_line[:100]!r}"
    )


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