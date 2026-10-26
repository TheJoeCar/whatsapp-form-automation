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

**`requirements.yaml`** — *your requirements document*: which forms qualify, and
what answers to give. The pipeline only fills forms that pass every rule.

```yaml
identity:            # answers for questions about you
  name: "Your Name"
  email: "you@example.com"
  phone: "+15551234567"
  extras:            # keyword-matched facts beyond name/email/phone
    - match: ["age", "how old"]
      value: "34"

rules:               # gates - a form that fails any of these is skipped (and logged why)
  title_must_include: []        # form title must contain one of these (empty = any)
  title_must_not_include: []    # form title must contain none of these
  min_fields: 1
  max_fields: 30
  disallowed_types: []          # skip forms containing these question types
  disallowed_keywords: []       # skip forms where any question mentions these
  required_all_answerable: true # every required field must be answerable from identity/options
  fill_optional: false          # answer required questions only
  never_guess: true             # abort instead of writing placeholder answers
```

If `requirements.yaml` is missing the pipeline falls back to legacy test mode
(`forms.test_data` in config.yaml, everything filled) with a logged warning.

| config.yaml | What | Default |
|---|---|---|
| `llm` | provider, model, endpoint | OpenRouter, `openrouter/free` (auto-picks a free model) |
| `whatsapp` | channel name, poll interval, session dir, selectors | 60s polling, headless |
| `forms.identity_file` | path to the requirements document | `requirements.yaml` |
| `pipeline` | state file, log file, **`dry_run`** | **dry run ON** |

## Usage

```bash
alias wa=".venv/bin/python -m src.cli"

wa init                 # config health check
wa check-llm            # tiny prompt to verify the model works
wa list-free-models     # see which free models exist right now

# Requirements + forms (no WhatsApp involved)
wa check-requirements <url>  # does the form satisfy requirements.yaml? where would answers come from?
wa parse-form <url>          # show how the parser reads your form - calibrate here
wa fill-form <url>           # print the payload that would be submitted (no submit; --force bypasses the gate)
wa submit-form <url>         # parse + fill + submit (asks for confirmation; --force bypasses the gate)

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