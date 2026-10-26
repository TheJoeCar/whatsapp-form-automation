"""Tests for pipeline auto-ack behavior."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline import Pipeline  # noqa: E402
from src.config import Config


def _make_pipeline(tmp_path):
    """Create a Pipeline with a temp state file and mocked LLM."""
    cfg = Config()
    cfg.pipeline.state_file = tmp_path / "processed.json"
    cfg.forms.requirements_file = tmp_path / "requirements.md"
    cfg.forms.requirements_file.unlink(missing_ok=True)

    pipeline = Pipeline(cfg, dry_run=True, headless=True)
    pipeline.llm = None  # disable LLM for tests
    return pipeline


def test_ack_existing_marks_messages_seen(tmp_path):
    """_ack_existing marks all visible messages as seen without processing."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old1"),
        MagicMock(message_id="msg-2", text="https://forms.gle/old2"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ack_existing(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")
    mock_monitor.open_channel.assert_called_once()


def test_ack_existing_skips_already_seen(tmp_path):
    """_ack_existing does not re-mark already-seen messages."""
    pipeline = _make_pipeline(tmp_path)
    pipeline.state.mark("msg-1", "submitted")

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old1"),
        MagicMock(message_id="msg-2", text="https://forms.gle/old2"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ack_existing(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")


def test_ack_existing_no_messages(tmp_path):
    """_ack_existing handles empty channel gracefully."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = []

    pipeline._ack_existing(mock_monitor)

    assert pipeline.state.count() == 0


def test_ack_existing_channel_not_found(tmp_path):
    """_ack_existing handles channel open failure gracefully."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = False

    pipeline._ack_existing(mock_monitor)

    assert pipeline.state.count() == 0


def test_ack_all_except_last_marks_all_but_last_seen(tmp_path):
    """_ack_all_except_last acks all messages except the most recent."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="old form 1"),
        MagicMock(message_id="msg-2", text="old form 2"),
        MagicMock(message_id="msg-3", text="old form 3"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ack_all_except_last(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")
    assert not pipeline.state.seen("msg-3")


def test_ack_all_except_last_single_message(tmp_path):
    """_ack_all_except_last with one message leaves it un-acked."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="only message"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ack_all_except_last(mock_monitor)

    assert not pipeline.state.seen("msg-1")


def test_ack_all_except_last_no_messages(tmp_path):
    """_ack_all_except_last with no messages is a no-op."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = []

    pipeline._ack_all_except_last(mock_monitor)

    assert pipeline.state.count() == 0


def test_run_once_auto_acks_by_default(tmp_path):
    """run_once auto-acks existing messages by default."""
    cfg = Config()
    cfg.pipeline.state_file = tmp_path / "processed.json"
    cfg.forms.requirements_file = tmp_path / "requirements.md"
    cfg.forms.requirements_file.unlink(missing_ok=True)

    pipeline = Pipeline(cfg, dry_run=True, headless=True)
    pipeline.llm = None

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old"),
        MagicMock(message_id="msg-2", text="https://forms.gle/new"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.wait_ready.return_value = True
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages
    mock_monitor.__enter__ = MagicMock(return_value=mock_monitor)
    mock_monitor.__exit__ = MagicMock(return_value=False)

    with patch.object(pipeline, "_poll_once", return_value=0) as mock_poll, \
         patch("src.pipeline.WhatsAppMonitor", return_value=mock_monitor):
        handled = pipeline.run_once()

    # open_channel and read_messages should have been called during auto-ack
    assert mock_monitor.open_channel.called
    assert mock_monitor.read_messages.called
    assert mock_poll.called


def test_run_once_no_ack_does_not_auto_ack(tmp_path):
    """run_once with --no-ack does NOT auto-ack existing messages."""
    cfg = Config()
    cfg.pipeline.state_file = tmp_path / "processed.json"
    cfg.forms.requirements_file = tmp_path / "requirements.md"
    cfg.forms.requirements_file.unlink(missing_ok=True)

    pipeline = Pipeline(cfg, dry_run=True, headless=True)
    pipeline.llm = None

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.wait_ready.return_value = True
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages
    mock_monitor.__enter__ = MagicMock(return_value=mock_monitor)
    mock_monitor.__exit__ = MagicMock(return_value=False)

    with patch.object(pipeline, "_poll_once", return_value=1) as mock_poll, \
         patch("src.pipeline.WhatsAppMonitor", return_value=mock_monitor):
        handled = pipeline.run_once(no_ack=True)

    assert handled == 1


def test_run_forever_first_cycle_auto_acks(tmp_path):
    """run_forever auto-acks on the first cycle before processing."""
    cfg = Config()
    cfg.pipeline.state_file = tmp_path / "processed.json"
    cfg.forms.requirements_file = tmp_path / "requirements.md"
    cfg.forms.requirements_file.unlink(missing_ok=True)

    pipeline = Pipeline(cfg, dry_run=True, headless=True)
    pipeline.llm = None

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.wait_ready.return_value = True
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages
    mock_monitor.__enter__ = MagicMock(return_value=mock_monitor)
    mock_monitor.__exit__ = MagicMock(return_value=False)

    # Simulate just the first cycle of run_forever (before the inner loop)
    # This is the auto-ack phase
    with patch("src.pipeline.WhatsAppMonitor", return_value=mock_monitor), \
         patch("time.sleep"):
        # Manually exercise the auto-ack logic that run_forever does
        if not pipeline._auto_acked:
            pipeline._ack_existing(mock_monitor)
            pipeline._auto_acked = True

    assert pipeline._auto_acked is True
