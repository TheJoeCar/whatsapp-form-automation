"""Tests for the processed-message state store and the `wa state` command."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.state import StateStore  # noqa: E402


def test_state_store_mark_and_seen(tmp_path):
    store = StateStore(tmp_path / "processed.json")
    assert not store.seen("msg-1")
    store.mark("msg-1", "submitted")
    assert store.seen("msg-1")
    assert not store.seen("msg-2")


def test_state_store_persists(tmp_path):
    store = StateStore(tmp_path / "processed.json")
    store.mark("msg-1", "submitted")
    store.save()

    store2 = StateStore(tmp_path / "processed.json")
    assert store2.seen("msg-1")


def test_state_store_ack_now_seen(tmp_path):
    """After marking a message as acknowledged, it is seen."""
    store = StateStore(tmp_path / "processed.json")
    store.mark("msg-1", "acknowledged")
    assert store.seen("msg-1")


def test_state_store_forget_makes_message_new_again(tmp_path):
    """forget() removes a message so it counts as new again."""
    store = StateStore(tmp_path / "processed.json")
    store.mark("msg-1", "submitted")
    assert store.seen("msg-1")

    assert store.forget("msg-1") is True
    assert not store.seen("msg-1")
    assert store.count() == 0


def test_state_store_forget_unknown_message(tmp_path):
    """forget() on a stored message returns False and changes nothing."""
    store = StateStore(tmp_path / "processed.json")
    store.mark("msg-1", "submitted")

    assert store.forget("msg-2") is False
    assert store.seen("msg-1")
    assert store.count() == 1


def test_state_store_forget_persists(tmp_path):
    """forget() is persisted across store instances."""
    store = StateStore(tmp_path / "processed.json")
    store.mark("msg-1", "submitted")
    store.forget("msg-1")

    store2 = StateStore(tmp_path / "processed.json")
    assert not store2.seen("msg-1")


def test_state_store_count(tmp_path):
    store = StateStore(tmp_path / "processed.json")
    assert store.count() == 0
    store.mark("msg-1", "acknowledged")
    store.mark("msg-2", "submitted")
    assert store.count() == 2


def test_state_store_corrupt_file_recovers(tmp_path):
    corrupt = tmp_path / "processed.json"
    corrupt.write_text("not json")
    store = StateStore(corrupt)
    assert store.count() == 0
    assert not corrupt.exists()  # renamed to .corrupt.bak


def test_cmd_state_clear_deletes_the_store(tmp_path, capsys):
    """cmd_state --clear forgets the history. No WhatsApp/browser is opened."""
    from src.cli import cmd_state
    from src.config import Config

    config = Config()
    config.pipeline.state_file = tmp_path / "processed.json"
    StateStore(config.pipeline.state_file).mark("msg-1", "submitted")

    args = MagicMock()
    args.clear = True

    with patch("src.cli.WhatsAppMonitor") as mock_monitor:
        cmd_state(config, args)

    assert not config.pipeline.state_file.exists()
    mock_monitor.assert_not_called()
    out = capsys.readouterr().out
    assert "state cleared" in out
    assert "--existing" in out


def test_state_has_no_ack_flag():
    """Ack behavior belongs to `wa run`, not a separate state command."""
    from src.cli import main

    try:
        main(["state", "--ack"])
    except SystemExit as exc:
        assert exc.code == 2  # argparse rejects unknown flags
    else:  # pragma: no cover - the flag must not exist
        raise AssertionError("`wa state --ack` should no longer be accepted")
