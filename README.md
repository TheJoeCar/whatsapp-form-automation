# WhatsApp → Google Forms Automation

A lightweight automation tool that monitors a WhatsApp channel for Google Forms
links, fills the form with test data, and submits it. Built for testing your
own forms — dry-run is on by default.

## Setup

```bash
# 1. Create a virtual environment
uv venv --python 3.12
uv pip install -r requirements.txt --python .venv/bin/python
.venv/bin/python -m playwright install chromium

# 2. Make the wa wrapper script executable (add to PATH or use ./wa)
chmod +x wa
# Optional: ln -s $(pwd)/wa /usr/local/bin/wa

# 3. Add your OpenRouter API key
cp .env.example .env
# Edit .env and paste your key from https://openrouter.ai/keys

# 3. Configure config.yaml
# Set your WhatsApp channel name and adjust any defaults

# 4. (Optional) Enable shell tab-completion
# Add the venv bin to your PATH (or symlink wa to a PATH directory):
export PATH="$PWD/.venv/bin:$PATH"
# Then register completion:
eval "$(register-python-argcomplete wa)"  # add to ~/.bashrc for persistence
```

## Configuration

Two files control behavior:

**`config.yaml`** — plumbing (model, channel, selectors, dry-run default).

**`requirements.md`** — a natural-language description of who you are and what
forms you fill. The automation reads this to decide if a form qualifies and
what values to use.

Example `requirements.md`:
```markdown
## Who I Am (Test Persona)
- **Name:** Your Name
- **Email:** your@email.com
- **Phone:** +15551234567

## What Kinds of Forms I Fill
Describe the types of forms you typically fill out here.

## Rules
- Only fill forms you would realistically sign up for.
- Answer required questions first.
- Never write placeholder text into required fields.
- For multiple-choice fields, always pick a real option.
```

If `requirements.md` is missing, the pipeline falls back to deterministic
heuristics (every form is filled) with a logged warning.

| Setting | Description | Default |
|---|---|---|
| `llm.model` | Model to use for value generation | `openrouter/free` |
| `whatsapp.channel` | WhatsApp channel name to monitor | *(set in config)* |
| `whatsapp.poll_interval_seconds` | Seconds between polls | `60` |
| `forms.requirements_file` | Path to requirements document | `requirements.md` |
| `pipeline.dry_run` | If `true`, no submissions are made | `true` |

## Usage

```bash
alias wa="./wa"

# Verification
wa init                      # check config health
wa check-llm                 # verify the model responds
wa list-free-models          # see available zero-cost models

# Form testing (no WhatsApp needed)
wa check-requirements <url>  # does the form match requirements.md?
wa parse-form <url>          # show how the parser reads your form
wa fill-form <url>           # print the payload that would be submitted
wa submit-form <url>         # parse + fill + submit (asks for confirmation)

# WhatsApp
wa login-whatsapp            # ONE TIME: scan the QR code
wa debug-whatsapp            # selector diagnostics if extraction breaks
wa list-channels             # list all chats/channels with their names

# The pipeline
wa run                       # one poll cycle (auto-acks existing messages by default)
wa run --loop                # keep polling until Ctrl+C
wa run --submit              # actually submit (only after dry-run looks right)
wa run --no-ack              # process existing messages too (don't auto-ack)
wa run --no-ack-last         # ack all messages except the most recent
wa state --clear             # reset processed messages
wa state --ack               # mark visible channel messages as seen (no submission)

# Shell tab-completion (requires the wa wrapper script)
eval "$(wa completion bash)"   # add to ~/.bashrc for persistence
```

## How It Works

1. The monitor opens WhatsApp Web and watches your configured channel
2. New messages are scanned for Google Forms links (including `forms.gle` short links)
3. Each form is parsed to extract fields, types, and options
4. The LLM reads `requirements.md` and decides if the form qualifies
5. If it qualifies, the LLM generates test values (validated against real options)
6. Values are submitted via HTTP POST to the form's `formResponse` endpoint
7. The result is logged and the message is marked as processed

### Auto-Acknowledgment

By default, when the pipeline starts it automatically acknowledges (marks as seen)
all currently visible messages in the channel before processing. This prevents
old form links from being re-processed on every restart. Only messages that
arrive *after* the acknowledgment are handled.

This behavior can be overridden:

| Flag | Effect |
|---|---|
| (default) | Auto-ack existing messages, process only new ones |
| `--no-ack` | Process all visible messages including old ones |
| `--no-ack-last` | Ack all messages except the most recent; process only the last |

The `wa state --ack` and `wa state --clear` commands provide manual control
over the processed-message store.

## Swapping in a Local Model

The LLM client is OpenAI-compatible, so switching to a local Ollama model is a
two-line config change:

```yaml
llm:
  provider: ollama
  base_url: http://localhost:11434/v1
  model: llama3.1:8b
```

No API key needed for Ollama; same code path.

## Troubleshooting

- **Form fields show `(title not found)`** or wrong types: Google changed their
  DOM. Run `wa parse-form <url>` to inspect, then adjust the heuristics in
  `src/forms/parser.py`.
- **No messages found**: run `wa debug-whatsapp --headed` to check selector
  counts, or `wa list-channels` to verify the channel name. WhatsApp changes
  its DOM periodically — `scripts/inspect_whatsapp.py` and
  `scripts/inspect_chat.py` dump the live structure.
- **LLM errors / rate limits**: free models have rate limits; use
  `wa list-free-models` to switch models. The filler falls back to deterministic
  heuristics when the LLM fails, so the pipeline still works.
- **Submission rejected**: single-page forms only. Check the form is accepting
  responses and CAPTCHA isn't blocking the submission.
- **Old form links getting re-processed**: by default the pipeline auto-acks
  all visible messages before processing, so only new messages are handled.
  Use `wa run --no-ack` if you intentionally want to re-process old links.
- **Tab-completion not working**: make sure `.venv/bin` is in your PATH
  (`export PATH="$PWD/.venv/bin:$PATH"`) and that `register-python-argcomplete`
  is available. Then run `eval "$(register-python-argcomplete wa)"` to enable
  completion. Add this to your `~/.bashrc` or `~/.zshrc` for persistence.
- **LLM refuses to fill the form**: the free model may safety-filter prompts.
  The filler falls back to no values (nothing submitted). Use neutral language
  in `requirements.md` (e.g. "test data" not "personal data").

## Project Structure

```
whatsapp-form-automation/
├── config.yaml              # plumbing settings
├── requirements.md          # your personal requirements (gitignored)
├── requirements.md.example  # template for new users
├── src/
│   ├── cli.py               # command-line entry point
│   ├── config.py            # configuration loader
│   ├── pipeline.py          # orchestration: poll → parse → fill → submit
│   ├── requirements.py      # requirements document loader & LLM prompts
│   ├── state.py             # processed-message store
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py        # OpenAI-compatible LLM client
│   ├── forms/
│   │   ├── __init__.py
│   │   ├── parser.py        # form structure extraction
│   │   ├── filler.py        # value generation + validation
│   │   └── submitter.py     # formResponse POST + success detection
│   └── whatsapp/
│       ├── __init__.py
│       └── monitor.py       # Playwright session + message reading
├── scripts/
│   ├── inspect_whatsapp.py  # dump live WhatsApp DOM structure
│   └── inspect_chat.py      # inspect a specific chat's message DOM
├── tests/                   # offline unit tests
├── data/                    # browser profile (gitignored)
├── logs/                    # pipeline logs (gitignored)
└── state/                   # processed messages (gitignored)
```

## License

MIT
```