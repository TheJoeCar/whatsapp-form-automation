"""Processed-message store: prevents re-handling the same WhatsApp message."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                # Corrupt file: keep a backup rather than crash the pipeline.
                self.path.rename(self.path.with_suffix(".corrupt.bak"))
        return {"processed": {}}

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            tmp.replace(self.path)

    def seen(self, message_id: str) -> bool:
        return message_id in self._data["processed"]

    def mark(self, message_id: str, result: str) -> None:
        with self._lock:
            self._data["processed"][message_id] = {"result": result, "ts": time.time()}
        self.save()

    def count(self) -> int:
        return len(self._data["processed"])