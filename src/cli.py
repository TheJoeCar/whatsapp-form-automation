"""Monitor a WhatsApp channel for Google Forms links, fill them with test data, and submit.

Dry-run is off by default — forms are submitted for real. Use --dry-run to preview without submitting.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT, Config, load_config
from .forms.filler import fill_form
from .forms.parser import fetch_form_html, is_google_forms_url, parse_form_html
from .forms.submitter import submit_form
from .llm.client import LLMClient, extract_json, list_free_models
from .pipeline import Pipeline
from .requirements import (
    evaluate_form_prompt,
    fill_form_prompt,
    load_requirements,
)
from .state import StateStore
from .whatsapp.monitor import WhatsAppMonitor, login_interactive

try:
    import argcomplete
except ImportError:
    argcomplete = None


def _setup_logging(config: Config, verbose: bool) -> None:
    config.pipeline.log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(config.pipeline.log_file, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _build_llm(config: Config) -> LLMClient | None:
    cfg = config.llm
    api_key = cfg.resolve_api_key()
    if cfg.provider == "openrouter" and not api_key:
        print(f"note: {cfg.api_key_env} is not set - LLM disabled, heuristics only")
        return None
    return LLMClient(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key=api_key,
        temperature=cfg.temperature,
        max_retries=cfg.max_retries,
    )


def _fetch_parsed_form(url: str):
    if not is_google_forms_url(url):
        sys.exit(f"{url} does not look like a Google Forms link")
    html, viewform = fetch_form_html(url)
    form = parse_form_html(html, url=viewform)
    print(form.summary())
    print(f"submit_url: {form.submit_url}")
    print(f"fbzx: {form.fbzx}")
    return form


# --------------------------------------------------------------------------- #
# Subcommands

def cmd_run(config: Config, args) -> None:
    _setup_logging(config, verbose=args.verbose)
    pipeline = Pipeline(
        config,
        dry_run=None if args.dry_run is None else args.dry_run,
        headless=False if args.headed else None,
        channel=args.channel,
        poll_interval=args.poll_interval,
    )
    mode = "DRY-RUN" if pipeline.dry_run else "LIVE"
    log.info("=== %s mode ===", mode)
    if args.loop:
        pipeline.run_forever(
            no_ack=args.no_ack,
            no_ack_last=args.no_ack_last,
        )
    else:
        handled = pipeline.run_once(
            no_ack=args.no_ack,
            no_ack_last=args.no_ack_last,
        )
        print(f"done - {handled} new message(s) handled "
              f"(mode: {mode}, log: {config.pipeline.log_file})")


def cmd_parse_form(config: Config, args) -> None:
    _fetch_parsed_form(args.url)


def cmd_fill_form(config: Config, args) -> None:
    form = _fetch_parsed_form(args.url)
    reqs_text = load_requirements(config.forms.requirements_file)
    values, warnings = fill_form(
        form,
        llm=_build_llm(config),
        requirements_text=reqs_text,
    )
    for warning in warnings:
        print(f"warning: {warning}")
    print(f"\nFilled values (what would be submitted to the form):")
    print(json.dumps(values, indent=2, ensure_ascii=False))


def cmd_submit_form(config: Config, args) -> None:
    form = _fetch_parsed_form(args.url)
    reqs_text = load_requirements(config.forms.requirements_file)
    values, warnings = fill_form(
        form,
        llm=_build_llm(config),
        requirements_text=reqs_text,
    )
    for warning in warnings:
        print(f"warning: {warning}")
    print(f"\nSubmitting {len(values)} field(s) to {form.submit_url}")
    if not args.yes:
        answer = input("Submit this form now? [y/N] ")
        if answer.strip().lower() != "y":
            print("Submission cancelled. No data was sent.")
            return
    result = submit_form(form, values)
    print(("✓ Submitted: " if result.ok else "✗ Failed: ") + result.detail)


def cmd_check_requirements(config: Config, args) -> None:
    """Dry check: does this form match the requirements document?

    Uses the LLM to evaluate the form against requirements.md.
    Falls back to a simple field-summary if the LLM is unavailable.
    """
    form = _fetch_parsed_form(args.url)
    reqs_text = load_requirements(config.forms.requirements_file)

    if reqs_text and _build_llm(config) is not None:
        llm = _build_llm(config)
        field_summaries = [
            {
                "entry_id": f.entry_id,
                "type": f.type,
                "required": f.required,
                "title": f.title,
                "options": f.options,
            }
            for f in form.fields
        ]
        prompt = evaluate_form_prompt(reqs_text, form.title or "(untitled)", field_summaries)
        reply = llm.complete(
            "Read the requirements document and decide if this form qualifies. "
            "Reply with ONLY a JSON object: {\"qualifies\": true/false, \"reason\": \"...\"}",
            prompt,
            max_tokens=300,
        )
        try:
            gate = extract_json(reply)
            if gate.get("qualifies"):
                print("\nGATE: PASS")
            else:
                print("\nGATE: FAIL")
                print(f"  Reason: {gate.get('reason', 'unknown')}")
        except Exception:
            print("\nGATE: could not parse LLM response — showing form summary instead")
    elif reqs_text:
        print(f"\nRequirements document loaded ({config.forms.requirements_file})")
        print("LLM unavailable — showing form summary for manual review:")
    else:
        print(f"\nNo requirements document at {config.forms.requirements_file}")
        print("Every form would be filled (legacy mode)")

    print(f"\nForm: {form.title or '(untitled)'}")
    print(f"Fields ({len(form.fields)}):")
    for field in form.fields:
        req = " [required]" if field.required else ""
        opts = f" options={field.options}" if field.options else ""
        print(f"  {field.entry_id}{req} type={field.type} title={field.title!r}{opts}")


def cmd_login(config: Config, args) -> None:
    ok = login_interactive(config.whatsapp.session_dir)
    sys.exit(0 if ok else 1)


def cmd_list_channels(config: Config, args) -> None:
    """List all WhatsApp chats/channels with their names."""
    headless = _resolve_headed(config, args)
    monitor = WhatsAppMonitor(config.whatsapp, headless=headless)
    monitor.start()
    try:
        if not monitor.wait_ready(quiet=False):
            print("WhatsApp Web not ready — are you logged in?")
            return
        from .whatsapp.monitor import CHAT_ROWS

        rows = monitor.page.locator(CHAT_ROWS)
        print(f"Found {rows.count()} chat row(s):")
        for i in range(min(30, rows.count())):
            text = (rows.nth(i).inner_text() or "").strip().replace("\n", " | ")
            if text:
                print(f"  {i + 1}. {text[:100]}")
    finally:
        monitor.stop()


def _resolve_headed(config: Config, args) -> bool:
    """Resolve headless flag from args, falling back to config default."""
    if hasattr(args, 'headed') and args.headed:
        return False
    if hasattr(args, 'headless') and args.headless:
        return True
    return config.whatsapp.headless


def cmd_debug_whatsapp(config: Config, args) -> None:
    """Open WhatsApp Web and dump selector diagnostics."""
    headless = _resolve_headed(config, args)
    monitor = WhatsAppMonitor(config.whatsapp, headless=headless)
    monitor.start()
    try:
        ready = monitor.wait_ready(quiet=False)
        print(f"\nWhatsApp ready: {ready}")
        if ready:
            print(monitor.diagnostics())
            if input("\nOpen the configured channel and dump message candidates? [y/N] ").lower().startswith("y"):
                if monitor.open_channel(config.whatsapp.channel):
                    print(monitor.diagnostics())
                    for msg in monitor.read_messages(limit=5):
                        print(f"  id={msg.message_id} meta={msg.meta!r} text={msg.text[:80]!r}")
    finally:
        monitor.stop()


def cmd_check_llm(config: Config, args) -> None:
    llm = _build_llm(config)
    if llm is None:
        sys.exit("LLM not configured (missing API key or provider disabled)")
    reply = llm.complete(
        "Reply with exactly one word.", "What is the opposite of 'empty'?"
    )
    print(f"model {config.llm.model} replied: {reply!r}")


def cmd_list_free_models(config: Config, args) -> None:
    models = list_free_models(config.llm.base_url)
    print(f"{len(models)} free models on OpenRouter:")
    for model in models:
        print(f"  {model}")


def cmd_state(config: Config, args) -> None:
    store = StateStore(config.pipeline.state_file)
    print(f"{store.count()} processed message(s) in {store.path}")
    if args.clear:
        store.path.unlink(missing_ok=True)
        print("state cleared")
    if args.ack:
        headless = _resolve_headed(config, args)
        monitor = WhatsAppMonitor(config.whatsapp, headless=headless)
        monitor.start()
        try:
            if not monitor.wait_ready(quiet=False):
                print("WhatsApp Web not ready — are you logged in?")
                return
            if not monitor.open_channel(config.whatsapp.channel):
                print(f"Could not open channel {config.whatsapp.channel!r}. "
                      f"Run 'wa list-channels' to see available channels.")
                return
            messages = monitor.read_messages()
            newly = 0
            for msg in messages:
                if not store.seen(msg.message_id):
                    store.mark(msg.message_id, "acknowledged")
                    newly += 1
            store.save()
            print(f"acknowledged {newly} message(s) ({len(messages)} total visible)")
        finally:
            monitor.stop()


def cmd_completion(config: Config, args) -> None:
    """Print shell completion setup for the given shell.

    Usage:
        eval "$(wa completion bash)"   # add to ~/.bashrc
        eval "$(wa completion zsh)"    # add to ~/.zshrc
        eval "$(wa completion fish)"   # add to ~/.config/fish/config.fish
    """
    shell = args.shell if hasattr(args, 'shell') else "bash"
    if argcomplete is None:
        sys.exit("argcomplete is not installed — run: uv pip install argcomplete")
    if shell not in ("bash", "zsh", "fish"):
        sys.exit(f"unsupported shell: {shell}. Use bash, zsh, or fish")

    # Use register-python-argcomplete for proper setup
    import shutil
    register = shutil.which("register-python-argcomplete")
    if register:
        print(f"# To enable tab-completion for 'wa', add this to your ~/.{shell}rc:")
        print(f'eval "$({register} wa)"')
        print()
        print("# Or source the completion script directly:")

    print(argcomplete.shellcode(
        executables=["wa"],
        shell=shell,
        use_defaults=True,
    ))


def cmd_init(config: Config, args) -> None:
    """Write .env from .env.example if needed and check configuration health."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        print(f"copy .env.example to {env_path} and add your OpenRouter key")
    key = config.llm.resolve_api_key()
    print(f"api key: {'✓ present' if key else '✗ not found — add it to .env'}")
    print(f"LLM: {config.llm.model} (via {config.llm.provider})")
    print(f"channel: {config.whatsapp.channel!r}")
    print(f"submission mode: {'DRY RUN (no forms will be submitted)' if config.pipeline.dry_run else 'LIVE (forms will be submitted)'}")
    session = Path(config.whatsapp.session_dir)
    print(f"whatsapp session: {'✓ exists' if session.exists() else '✗ not yet created (run wa login-whatsapp)'}")
    reqs = Path(config.forms.requirements_file)
    print(f"requirements doc: {'✓ exists' if reqs.exists() else '✗ not found — all forms will be filled without filtering'}")

    # Next steps
    issues = []
    if not key:
        issues.append("add your OpenRouter API key to .env")
    if config.whatsapp.channel == "My Channel":
        issues.append("set 'whatsapp.channel' in config.yaml to your WhatsApp channel name")
    if not session.exists():
        issues.append("run 'wa login-whatsapp' to set up WhatsApp Web session")
    if not reqs.exists():
        issues.append("create a requirements.md file (copy from requirements.md.example)")

    if issues:
        print("\nNext steps:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
    else:
        print("\nAll checks passed! Run 'wa run' to start processing forms.")


# --------------------------------------------------------------------------- #

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="whatsapp-form-automation", description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="poll WhatsApp and process new form links")
    p.add_argument("--loop", action="store_true", help="keep polling until Ctrl+C")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                   help="preview only: print what would be submitted without actually sending")
    p.add_argument("--submit", dest="dry_run", action="store_false",
                   help="submit forms for real (this is the default)")
    p.add_argument("--headed", action="store_true", help="run WhatsApp browser headful (debugging)")
    p.add_argument("--no-ack", action="store_true",
                   help="process existing messages too (don't auto-acknowledge)")
    p.add_argument("--no-ack-last", action="store_true",
                   help="ack all messages except the most recent one (process only the last)")
    p.add_argument("--channel", default=None,
                   help="override the configured WhatsApp channel")
    p.add_argument("--poll-interval", type=int, default=None,
                   help="override the poll interval in seconds")
    p.add_argument("-v", "--verbose", action="store_true", help="also log to console")

    p = sub.add_parser("parse-form", help="fetch and parse a Google Form, print fields")
    p.add_argument("url")

    p = sub.add_parser("fill-form", help="parse a form and print the filled payload (no submit)")
    p.add_argument("url")

    p = sub.add_parser("submit-form", help="parse, fill, and submit a form (asks for confirmation)")
    p.add_argument("url")
    p.add_argument("--yes", action="store_true", help="skip confirmation prompt")

    p = sub.add_parser("check-requirements", help="test a form URL against requirements.md")
    p.add_argument("url")

    sub.add_parser("login-whatsapp", help="one-time headful QR login (session is saved)")

    p = sub.add_parser("debug-whatsapp", help="open WhatsApp Web and dump selector diagnostics")
    p.add_argument("--headed", action="store_true", help="run headful (show browser window)")
    p.add_argument("--headless", action="store_true", help="run headless (default)")

    p = sub.add_parser("list-channels", help="list all WhatsApp chats/channels with their names")
    p.add_argument("--headed", action="store_true", help="run headful (show browser window)")
    p.add_argument("--headless", action="store_true", help="run headless (default)")

    sub.add_parser("check-llm", help="send a tiny prompt to the configured model")

    sub.add_parser("list-free-models", help="list zero-cost models currently on OpenRouter")

    p = sub.add_parser("state", help="show/clear/acknowledge the processed-message store")
    p.add_argument("--clear", action="store_true", help="delete the state file entirely")
    p.add_argument("--ack", action="store_true", help="mark all visible messages in the channel as seen (no processing)")
    p.add_argument("--headed", action="store_true", help="run headful (for ack)")

    sub.add_parser("init", help="check configuration health")

    p = sub.add_parser("completion", help="print shell completion script")
    p.add_argument("shell", choices=["bash", "zsh", "fish"],
                    help="target shell")

    if argcomplete:
        argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    handlers = {
        "run": cmd_run,
        "parse-form": cmd_parse_form,
        "fill-form": cmd_fill_form,
        "submit-form": cmd_submit_form,
        "check-requirements": cmd_check_requirements,
        "login-whatsapp": cmd_login,
        "debug-whatsapp": cmd_debug_whatsapp,
        "list-channels": cmd_list_channels,
        "check-llm": cmd_check_llm,
        "list-free-models": cmd_list_free_models,
        "state": cmd_state,
        "init": cmd_init,
        "completion": cmd_completion,
    }
    handlers[args.command](config, args)


if __name__ == "__main__":
    main()