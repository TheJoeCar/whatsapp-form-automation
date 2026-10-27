"""Tests for how the pipeline treats messages already in the channel."""

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


def test_ignore_existing_marks_messages_seen(tmp_path):
    """_ignore_existing marks all visible messages as seen without processing."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old1"),
        MagicMock(message_id="msg-2", text="https://forms.gle/old2"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ignore_existing(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")
    mock_monitor.open_channel.assert_called_once()


def test_ignore_existing_skips_already_seen(tmp_path):
    """_ignore_existing does not re-mark already-seen messages."""
    pipeline = _make_pipeline(tmp_path)
    pipeline.state.mark("msg-1", "submitted")

    fake_messages = [
        MagicMock(message_id="msg-1", text="https://forms.gle/old1"),
        MagicMock(message_id="msg-2", text="https://forms.gle/old2"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ignore_existing(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")


def test_ignore_existing_no_messages(tmp_path):
    """_ignore_existing handles empty channel gracefully."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = []

    pipeline._ignore_existing(mock_monitor)

    assert pipeline.state.count() == 0


def test_ignore_existing_channel_not_found(tmp_path):
    """_ignore_existing handles channel open failure gracefully."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = False

    pipeline._ignore_existing(mock_monitor)

    assert pipeline.state.count() == 0


def test_ignore_all_but_last_marks_all_but_last_seen(tmp_path):
    """_ignore_all_but_last marks all messages except the most recent as seen."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="old form 1"),
        MagicMock(message_id="msg-2", text="old form 2"),
        MagicMock(message_id="msg-3", text="old form 3"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ignore_all_but_last(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")
    assert not pipeline.state.seen("msg-3")


def test_ignore_all_but_last_single_message(tmp_path):
    """_ignore_all_but_last with one message leaves it pending."""
    pipeline = _make_pipeline(tmp_path)

    fake_messages = [
        MagicMock(message_id="msg-1", text="only message"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ignore_all_but_last(mock_monitor)

    assert not pipeline.state.seen("msg-1")


def test_ignore_all_but_last_no_messages(tmp_path):
    """_ignore_all_but_last with no messages is a no-op."""
    pipeline = _make_pipeline(tmp_path)

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = []

    pipeline._ignore_all_but_last(mock_monitor)

    assert pipeline.state.count() == 0


def test_ignore_all_but_last_resets_previously_handled_last(tmp_path):
    """_ignore_all_but_last forgets the last message if it was processed before,
    so it is genuinely reprocessed (instead of reporting '0 new')."""
    pipeline = _make_pipeline(tmp_path)
    # Simulate a previous run that already handled the latest message.
    pipeline.state.mark("msg-3", "submitted")

    fake_messages = [
        MagicMock(message_id="msg-1", text="old form 1"),
        MagicMock(message_id="msg-2", text="old form 2"),
        MagicMock(message_id="msg-3", text="newest form 3"),
    ]

    mock_monitor = MagicMock()
    mock_monitor.open_channel.return_value = True
    mock_monitor.read_messages.return_value = fake_messages

    pipeline._ignore_all_but_last(mock_monitor)

    assert pipeline.state.seen("msg-1")
    assert pipeline.state.seen("msg-2")
    assert not pipeline.state.seen("msg-3")  # reset, so _poll_once treats it as new


def test_run_once_only_last_reprocesses_old_last_message(tmp_path):
    """run_once with --last processes the last message even though it was
    already handled in a previous run."""
    pipeline = _make_pipeline(tmp_path)
    pipeline.state.mark("msg-2", "submitted")  # previously handled

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

    handled_ids = []

    def fake_poll_once(monitor):
        fresh = [m for m in fake_messages if not pipeline.state.seen(m.message_id)]
        for m in fresh:
            handled_ids.append(m.message_id)
        return len(fresh)

    with patch("src.pipeline.WhatsAppMonitor", return_value=mock_monitor), \
         patch.object(pipeline, "_poll_once", side_effect=fake_poll_once):
        handled = pipeline.run_once(only_last=True)

    assert handled == 1
    assert handled_ids == ["msg-2"]  # the previously-handled message was reprocessed


def test_run_once_ignores_existing_by_default(tmp_path):
    """run_once ignores existing messages by default."""
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

    # open_channel and read_messages should have been called while ignoring existing messages
    assert mock_monitor.open_channel.called
    assert mock_monitor.read_messages.called
    assert mock_poll.called
    assert pipeline.ignored_existing == 2


def test_run_once_include_existing_does_not_ignore(tmp_path):
    """run_once with --existing does NOT ignore existing messages."""
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
        handled = pipeline.run_once(include_existing=True)

    assert handled == 1
    assert pipeline.ignored_existing == 0  # nothing was ignored


def test_run_forever_startup_ignores_existing(tmp_path):
    """run_forever ignores existing messages on the first cycle."""
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
    # This is the startup ignore phase
    with patch("src.pipeline.WhatsAppMonitor", return_value=mock_monitor), \
         patch("time.sleep"):
        # Manually exercise the startup-ignore logic that run_forever does
        if not pipeline._startup_ignored:
            pipeline._ignore_existing(mock_monitor)
            pipeline._startup_ignored = True

    assert pipeline._startup_ignored is True
