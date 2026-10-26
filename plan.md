# WhatsApp → Google Forms Automation Plan

## Goal

A script the user runs on demand that:

1. Opens WhatsApp Web (their own account) and watches **one specific channel** they control
2. Detects new messages containing links, filters for **Google Forms links**
3. Uses an LLM to understand the form and produce sensible **test data**
4. Submits the form and logs the result

Scope guardrails: this is for testing **forms the user owns**. It is not designed
to bypass CAPTCHAs, bulk-submit, or fill other people's forms.

## Key design decisions

| Decision | Choice | Why |
|---|---|---|
| **Requirements document** | `requirements.yaml`: identity + rules, enforced by a gate before filling | You decide *which* forms qualify and *what* answers to give; ineligible forms are skipped with logged reasons |
| LLM hosting | **OpenRouter free models now**, local Ollama later | No model download needed to start; swap later by changing `base_url`/`model` in config |
| LLM client | Single **OpenAI-compatible** client | OpenRouter (`https://openrouter.ai/api/v1`) and Ollama (`http://localhost:11434/v1`) speak the same protocol → drop-in swap |
| Form submission | **HTTP POST to the form's `formResponse` endpoint**, not browser automation | Google Forms can be submitted programmatically with `entry.NN` params; far more reliable than clicking through a browser |
| Form structure parsing | Extract `entry.NN` ids + labels from the form HTML (DOM + the embedded `FB_PUBLIC_LOAD_DATA_` JSON), with multi-strategy fallbacks | Google's DOM obfuscates class names; entry ids in the HTML are the ground truth for submission |
| WhatsApp monitoring | **Playwright** with a persistent profile (QR login once), role-based selectors + config overrides | One language for whole project; WhatsApp Web DOM changes often, so selectors live in config with a debug command |
| Safety | **Dry-run by default**, explicit `submit` command, state file prevents re-processing messages, everything logged | Wrong submissions cost real form responses |

## Architecture

```
                ┌────────────────────────────┐
                │  config.yaml + .env        │  channel name, model, test persona, selectors
                └─────────────┬──────────────┘
                              ▼
┌──────────────────┐   ┌──────────────────────┐   ┌────────────────────┐
│ WhatsAppMonitor  │──▶│ Message filter       │──▶│ URL extraction     │
│ (Playwright,     │   │ (new msgs only, via  │   │ (regex + Google    │
│ persistent sess) │   │  state/processed.json)│  │  Forms detection)  │
└──────────────────┘   └──────────────────────┘   └─────────┬──────────┘
                                                            ▼
                  ┌────────────────────┐   ┌───────────────────────┐
                  │ FormParser         │◀──│ forms.gle resolution  │
                  │ (entry ids, types, │   └───────────────────────┘
                  │  options, required)│
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐   ┌───────────────────────┐
                  │ FormFiller         │◀──│ LLM (OpenRouter now,  │
                  │ (field → value map)│   │  Ollama later) +      │
                  └─────────┬──────────┘   │  deterministic fallback│
                            ▼              └───────────────────────┘
                  ┌────────────────────┐
                  │ FormSubmitter      │  POST docs.google.com/.../formResponse
                  └─────────┬──────────┘
                            ▼
                  ┌────────────────────┐
                  │ State + logs       │  processed.json, logs/pipeline.log
                  └────────────────────┘
```

## What the LLM does (and doesn't do)

**Does:**
- Given parsed form fields (title, type, options, required), return a JSON map
  of `entry.NN → value`, respecting option lists and the configured test persona
- Assists URL/message disambiguation when regex is ambiguous

**Doesn't:**
- Handle the submission protocol (pure HTTP, no LLM involved)
- Solve CAPTCHAs (Google Forms may still show one after repeated submissions —
  expected and accepted for light testing of your own forms)

## Project layout

```
whatsapp-form-automation/
├── plan.md                  # this file
├── README.md                # setup + usage
├── requirements.txt         # python deps
├── requirements.yaml        # form requirements document: identity + rules
├── config.yaml              # checked in, no secrets (api key comes from .env)
├── .env.example
├── .gitignore
├── src/
│   ├── cli.py               # entry point (run / parse-form / fill-form / submit-form / login / debug)
│   ├── config.py            # config + .env loading
│   ├── requirements.py      # requirements document: identity, rules, gate (evaluate_form)
│   ├── state.py             # processed-message store (JSON)
│   ├── llm/client.py        # OpenAI-compatible client (OpenRouter/Ollama) + free-model listing
│   ├── forms/parser.py      # URL resolution, entry-id extraction, field types/options
│   ├── forms/filler.py      # LLM value generation + deterministic fallback + validation
│   ├── forms/submitter.py   # formResponse POST + success detection
│   ├── whatsapp/monitor.py  # Playwright session, chat search, message reading
│   └── pipeline.py          # orchestration: poll → filter → parse → fill → submit → record
├── tests/                   # offline unit tests (URL logic, JSON extraction, filler validation)
├── data/whatsapp_session/   # persistent browser profile (gitignored)
├── logs/                    # pipeline.log (gitignored)
└── state/                   # processed.json (gitignored)
```

## Milestones

- [x] **M0 — Scaffold**: config, LLM client (OpenRouter free models), layout
- [x] **M1 — Forms module**: parser, filler (LLM + fallback), submitter, offline tests
      → *calibrate parser against one of your real forms with `parse-form <url>`*
- [x] **M2 — WhatsApp monitor**: login script (QR once), channel search, message polling, debug command
- [x] **M3 — Pipeline + CLI**: `run --once` end-to-end in dry-run, then real submit
- [x] **M3.5 — Requirements document**: `requirements.yaml` gate (rules) + identity-driven filling,
      `check-requirements` command, `never_guess` abort instead of placeholders
- [ ] **M4 — Hardening**: run against real channel for a few days; retry/backoff tuning;
      switch LLM to Ollama on the home lab when ready

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| WhatsApp Web DOM changes break extraction | Role-based selectors first, class-based fallbacks, selectors in config, `debug-whatsapp` dumps candidates |
| Form parser misreads field types | `parse-form` prints everything it found; filler validates option membership before submitting |
| Free OpenRouter models rate-limited / down | Retries with backoff; `list-free-models` command to switch; deterministic filler fallback works without LLM |
| Duplicate submissions from re-polled messages | `state/processed.json` keyed on message id |
| Accidental real submissions | dry-run default everywhere; `submit-form` asks for confirmation |
| WhatsApp account flags automation | Human-pace polling (default 60s), only reading one channel, no bulk messaging actions |

## Test checklist

1. `check-llm` → model responds
2. Edit `requirements.yaml`: identity + rules; `check-requirements <url>` → gate verdict + answer sources
3. `parse-form <your form url>` → all fields/types/options correct
4. `fill-form <url>` (dry run) → payload sensible, options valid, required-only when `fill_optional: false`
5. `submit-form <url> --yes` on a throwaway form you own → response recorded
6. `run --once` with the monitor → dry-run plan printed for the channel message
7. Real run on a test form, then flip off dry-run for the real target