# WhatsApp → Google Forms Automation

Watches one WhatsApp channel you control for new messages containing Google
Forms links, fills the form with test data (LLM-assisted), and submits it.
**For testing forms you own** — dry-run is on by default.

See `plan.md` for the architecture, milestones and risk notes.

## Setup

```bash
# 1. Environment (uv-managed venv, Python 3.12 for wheel compatibility)
uv venv --python 3.12
uv pip install -r requirements.txt --python .venv/bin/python
.venv/bin/python -m playwright install chromium

# 2. LLM: copy .env.example to .env and add your OpenRouter API key
#    (free models - no cost. Key at https://openrouter.ai/keys)
cp .env.example .env && $EDITOR .env

# 3. Point config.yaml at your channel: whatsapp.channel
```

## Configuration

Two files control behavior:

**`config.yaml`** — plumbing (model, channel, selectors, dry-run default).

**`requirements.md`** — *your requirements document*: a natural-language
description of who you are, what forms you fill, and any constraints.
The LLM reads this to decide if a form qualifies and what values to use.

The requirements document is flexible — write it in plain English.
The LLM interprets it to:
- Decide if a form matches what you'd fill out
- Generate appropriate test values for each field
- Respect constraints (e.g. "never write placeholder text")

Example `requirements.md`:
```markdown
## Who I Am (Test Persona)
- **Name:** Joe Bobby
- **Email:** joe.bobby@example.com
- **Phone:** +15551234567

## What Kinds of Forms I Fill
I mainly fill out climbing trip forms. These typically ask about
personal info, climbing experience, and waivers.

## Rules
- Only fill forms I would realistically sign up for.
- Answer required questions first.
- Never write placeholder text into required fields.
- For multiple-choice fields, always pick a real option.
```

If `requirements.md` is missing the pipeline falls back to legacy test mode
(deterministic heuristics, every form filled) with a logged warning.

| config.yaml | What | Default |
|---|---|---|
| `llm` | provider, model, endpoint | OpenRouter, `openrouter/free` (auto-picks a free model) |
| `whatsapp` | channel name, poll interval, session dir, selectors | 60s polling, headless |
| `forms.requirements_file` | path to the requirements document | `requirements.md` |
| `pipeline` | state file, log file, **`dry_run`** | **dry run ON** |

## Usage

```bash
alias wa=".venv/bin/python -m src.cli"

wa init                 # config health check
wa check-llm            # tiny prompt to verify the model works
wa list-free-models     # see which free models exist right now

# Requirements + forms (no WhatsApp involved)
wa check-requirements <url>  # does the form match requirements.md?
wa parse-form <url>          # show how the parser reads your form - calibrate here
wa fill-form <url>           # print the payload that would be submitted (no submit)
wa submit-form <url>         # parse + fill + submit (asks for confirmation)

# WhatsApp
wa login-whatsapp       # ONE TIME: headful browser opens, scan the QR
wa debug-whatsapp       # selector diagnostics if extraction breaks

# The actual pipeline
wa run                  # one poll cycle (dry run by default)
wa run --loop           # keep polling until Ctrl+C
wa run --submit         # really submit (only after dry-run looks right)
wa state --clear        # forget processed messages (re-process on next poll)
```

The flow: new messages in the configured channel → URL extraction → Google
Forms link check (incl. `forms.gle` short links) → parse form → **requirements
gate** (`evaluate_form` against `rules`) → fill with your identity (LLM values
validated against real options; `never_guess` aborts instead of guessing) →
POST to the form's `formResponse` endpoint → result logged, message marked
processed in `state/processed.json`. Forms failing the gate are skipped with
the reason in the log.

## Swapping in a local model later (Ollama)

The LLM client is OpenAI-compatible, so switching is two config lines:

```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434/v1
  model: llama3.1:8b   # whatever you pull
```

No API key needed for Ollama; same code path.

## Troubleshooting

- **Form fields show `(title not found)`** or wrong types: Google changed their
  DOM. Run `wa parse-form <url>`, compare with the real form, adjust the
  heuristics in `src/forms/parser.py` (`_title_for_field`, `_label_for_input`).
- **No messages found / search fails**: run `wa debug-whatsapp`, check the
  selector match counts, adjust `whatsapp.selectors` in config.
- **LLM errors / rate limits**: free models have rate limits; `wa
  list-free-models` and pick another id in config. The filler falls back to
  deterministic heuristics when the LLM fails, so the pipeline still works.
- **Submission rejected**: single-page forms only for now (multi-page needs a
  POST per page). Check the form is accepting responses and CAPTCHA isn't up.