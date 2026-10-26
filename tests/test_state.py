"""Tests for the processed-message state store and ack command."""

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


def test_cmd_state_ack_marks_messages_seen():
    """cmd_state with --ack opens the channel and marks visible messages as seen."""
    from src.cli import cmd_state
    from src.config import Config

    config = Config()
    args = MagicMock()
    args.clear = False
    args.ack = True
    args.headed = False

    fake_messages = [
        MagicMock(message_id="msg-1", text="old form link"),
        MagicMock(message_id="msg-2", text="another old link"),
    ]

    mock_store = MagicMock()
    mock_store.count.return_value = 0
    mock_store.seen.side_effect = lambda mid: False  # none seen yet
    mock_store.mark = MagicMock()
    mock_store.path = Path("/tmp/processed.json")
    mock_store.save = MagicMock()

    mock_monitor = MagicMock()
    mock_monitor.wait_ready.return_value = True
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages
    mock_monitor.stop = MagicMock()

    with patch("src.cli.StateStore", return_value=mock_store), \
         patch("src.cli.WhatsAppMonitor", return_value=mock_monitor):
        cmd_state(config, args)

        # Both messages should have been marked as acknowledged
        assert mock_store.mark.call_count == 2
        mock_store.mark.assert_any_call("msg-1", "acknowledged")
        mock_store.mark.assert_any_call("msg-2", "acknowledged")
        mock_store.save.assert_called_once()


def test_cmd_state_ack_skips_already_seen():
    """cmd_state --ack skips messages already in the store."""
    from src.cli import cmd_state
    from src.config import Config

    config = Config()
    args = MagicMock()
    args.clear = False
    args.ack = True
    args.headed = False

    fake_messages = [
        MagicMock(message_id="msg-1", text="old form link"),
        MagicMock(message_id="msg-2", text="another old link"),
    ]

    mock_store = MagicMock()
    mock_store.count.return_value = 0
    mock_store.seen.side_effect = lambda mid: mid == "msg-1"  # msg-1 already seen
    mock_store.mark = MagicMock()
    mock_store.path = Path("/tmp/processed.json")
    mock_store.save = MagicMock()

    mock_monitor = MagicMock()
    mock_monitor.wait_ready.return_value = True
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages
    mock_monitor.stop = MagicMock()

    with patch("src.cli.StateStore", return_value=mock_store), \
         patch("src.cli.WhatsAppMonitor", return_value=mock_monitor):
        cmd_state(config, args)

        # Only msg-2 should be marked (msg-1 already seen)
        assert mock_store.mark.call_count == 1
        mock_store.mark.assert_called_once_with("msg-2", "acknowledged")
        mock_store.save.assert_called_once()
